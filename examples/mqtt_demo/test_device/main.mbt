///|
/// Independent simulated relay device.
///
/// This is a separate operating-system process from the controller and uses the
/// same `moon-mqtt-client` library. It is a *simulated* device: no hardware is
/// involved. State lives in its own memory, so restarting the controller while
/// this process keeps running is exactly the "controller restart while the
/// device is still ON" scenario, and restarting this process is the "device
/// reboot" scenario.
///
/// Wire behaviour:
/// - subscribes to commands and queries,
/// - applies an absolute target ON/OFF (idempotent),
/// - echoes feedback for every command and every query it accepts,
/// - republishes retained feedback on startup and on demand, never a claim on
///   behalf of the controller.
async fn run(config : @demo.DemoConfig, topics : @demo.TopicLayout) -> Unit {
  // `startup_id` changes on every device process, so retained feedback from an
  // earlier run is distinguishable from feedback produced by this run.
  let startup_id = config.client_id + "-" + now_millis().to_string()
  let mut state_on = false
  let mut sequence = 0
  @mqtt.with_client(config.mqtt_config(), async fn(client) {
    let granted = client.subscribe([
      { topic: topics.command, qos: @mqtt.AtLeastOnce },
      { topic: topics.query, qos: @mqtt.AtLeastOnce },
    ])
    @demo.log_line(
      "{\"role\":\"device\",\"startup_id\":\"\{startup_id}\",\"event\":\"subscribed\",\"results\":\{@debug.to_string(granted)}}",
    )
    for result in granted {
      if result is @mqtt.Rejected {
        @demo.log_line(
          "{\"role\":\"device\",\"event\":\"subscription_rejected\"}",
        )
        return
      }
    }
    sequence += 1
    publish_feedback(
      client,
      topics,
      {
        startup_id,
        sequence,
        state_on,
        command_id: None,
        query_id: None,
      },
    )
    let mut first = true
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) =>
          if message.topic == topics.command {
            match decode_text(message.payload).bind(@demo.decode_command) {
              Some(command) => {
                // Absolute set: applying ON to an ON device is a no-op, and a
                // repeated command id still produces feedback for correlation.
                state_on = command.target_on
                sequence += 1
                @demo.log_line(
                  "{\"role\":\"device\",\"event\":\"applied\",\"command_id\":\"\{command.command_id}\",\"state\":\"\{state_text(state_on)}\",\"sequence\":\{sequence}}",
                )
                publish_feedback(
                  client,
                  topics,
                  {
                    startup_id,
                    sequence,
                    state_on,
                    command_id: Some(command.command_id),
                    query_id: None,
                  },
                )
              }
              None =>
                @demo.log_line(
                  "{\"role\":\"device\",\"event\":\"invalid_command\",\"payload\":\{json_string(decode_text(message.payload).unwrap_or(""))}}",
                )
            }
          } else if message.topic == topics.query {
            match decode_text(message.payload).bind(@demo.decode_query) {
              Some(query_id) => {
                sequence += 1
                @demo.log_line(
                  "{\"role\":\"device\",\"event\":\"queried\",\"query_id\":\"\{query_id}\",\"state\":\"\{state_text(state_on)}\"}",
                )
                publish_feedback(
                  client,
                  topics,
                  {
                    startup_id,
                    sequence,
                    state_on,
                    command_id: None,
                    query_id: Some(query_id),
                  },
                )
              }
              None =>
                @demo.log_line(
                  "{\"role\":\"device\",\"event\":\"invalid_query\",\"payload\":\{json_string(decode_text(message.payload).unwrap_or(""))}}",
                )
            }
          }
        @mqtt.Connected(generation) => {
          // The device itself reconnected: report the current in-memory state.
          @demo.log_line(
            "{\"role\":\"device\",\"startup_id\":\"\{startup_id}\",\"event\":\"connected\",\"generation\":\{generation}}",
          )
          if !first {
            sequence += 1
            publish_feedback(
              client,
              topics,
              {
                startup_id,
                sequence,
                state_on,
                command_id: None,
                query_id: None,
              },
            )
          }
          first = false
        }
        @mqtt.Disconnected(_, reason) =>
          @demo.log_line(
            "{\"role\":\"device\",\"event\":\"disconnected\",\"reason\":\{json_string(reason)}}",
          )
      }
    }
  })
}

///|
async fn publish_feedback(
  client : @mqtt.Client,
  topics : @demo.TopicLayout,
  feedback : @demo.DeviceFeedback,
) -> Unit {
  let payload = @demo.encode_feedback(feedback)
  // Feedback is retained: a fresh controller can observe the latest device
  // report, but it must still correlate a *new* answer to its own query id
  // before treating it as current truth.
  client.publish(
    topics.feedback,
    @utf8.encode(payload),
    qos=@mqtt.AtLeastOnce,
    retain=true,
  ) catch {
    error =>
      @demo.log_line(
        "{\"role\":\"device\",\"event\":\"feedback_publish_failed\",\"error\":\{json_string(error.to_string())}}",
      )
  }
  @demo.log_line(
    "{\"role\":\"device\",\"event\":\"feedback\",\"payload\":\{json_string(payload)}}",
  )
}

///|
fn state_text(state_on : Bool) -> String {
  if state_on {
    "ON"
  } else {
    "OFF"
  }
}

///|
fn decode_text(payload : Bytes) -> String? {
  Some(@utf8.decode(payload)) catch {
    _ => None
  }
}

///|
fn json_string(value : String) -> String {
  @contracts.json_encode_string(value)
}

///|
fn now_millis() -> Int64 {
  @async.now()
}

///|
async fn main {
  let config = @demo.DemoConfig::from_env(
    "moon-demo-device",
    "MQTT_DEMO_DEVICE_ID",
  )
  let topics = @demo.TopicLayout::new(config.topic_prefix)
  @demo.log_line(
    "{\"role\":\"device\",\"event\":\"start\",\"broker\":\"\{config.host}:\{config.port}\",\"prefix\":\"\{config.topic_prefix}\"}",
  )
  run(config, topics)
}
