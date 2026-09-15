///|
/// Thermostat controller for the state-sync demo.
///
/// The controller only ever maintains a *desired* target. It reports
/// "executed" only after fresh device feedback arrives, and it keeps three
/// separate notions apart in its output:
///
///   desired  - what the hysteresis contract wants right now,
///   result   - what happened to the MQTT publish (sent / not_sent / unknown),
///   reported - what the device actually reports, correlated by id.
///
/// After an `unknown` publish outcome the controller does not assume success:
/// it queries the device and compares the answer with the desired target.
enum Outcome {
  Sent
  NotSent
  Unknown
}

///|
/// Publish and classify the transport result instead of treating every failure
/// as fatal. The QoS 1 acknowledgement is the first real evidence that the
/// broker accepted the command, so `NotSent` and `Unknown` are distinct
/// outcomes, not errors to swallow.
async fn publish_checked(
  client : @mqtt.Client,
  topic : String,
  payload : String,
  retain? : Bool = false,
) -> Outcome {
  try {
    client.publish(
      topic,
      @utf8.encode(payload),
      qos=@mqtt.AtLeastOnce,
      retain~,
    )
    Sent
  } catch {
    @mqtt.OutcomeUnknown(_) => Unknown
    @mqtt.NotSent(_) | @mqtt.Backpressure(_) | @mqtt.NotConnected | @mqtt.Closed =>
      NotSent
    error => {
      @demo.log_line(
        "{\"role\":\"controller\",\"event\":\"publish_error\",\"error\":\{@contracts.json_encode_string(error.to_string())}}",
      )
      Unknown
    }
  }
}

///|
fn outcome_text(outcome : Outcome) -> String {
  match outcome {
    Sent => "sent"
    NotSent => "not_sent"
    Unknown => "unknown"
  }
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
/// The controller's local view. `reported` is `None` until device feedback
/// confirms a state; the controller never assumes `false` for it.
struct ControllerState {
  mut desired_on : Bool
  mut reported : Bool?
  mut last_result : Outcome
  mut last_command_id : String?
  mut last_query_id : String?
  mut command_counter : Int
  mut query_counter : Int
}

///|
fn ControllerState::next_command_id(self : ControllerState) -> String {
  self.command_counter += 1
  "cmd-" + self.command_counter.to_string()
}

///|
fn ControllerState::next_query_id(self : ControllerState) -> String {
  self.query_counter += 1
  "qry-" + self.query_counter.to_string()
}

///|
async fn ControllerState::log(self : ControllerState, extra : String) -> Unit {
  @demo.log_line(
    "{\"role\":\"controller\",\"desired\":\"\{state_text(self.desired_on)}\",\"result\":\"\{outcome_text(self.last_result)}\",\"reported\":\{option_bool_json(self.reported)},\"last_command_id\":\{option_json(self.last_command_id)},\"last_query_id\":\{option_json(self.last_query_id)}\{extra}}",
  )
}

///|
fn option_json(value : String?) -> String {
  match value {
    Some(text) => @contracts.json_encode_string(text)
    None => "null"
  }
}

///|
fn option_bool_json(value : Bool?) -> String {
  match value {
    Some(on) => "\"" + state_text(on) + "\""
    None => "null"
  }
}

///|
/// Decide what to do for one temperature sample. `QueryDevice` is returned when
/// the relay state is genuinely unknown inside the deadband, and the caller
/// turns that into a query instead of guessing.
async fn handle_temperature(
  client : @mqtt.Client,
  topics : @demo.TopicLayout,
  state : ControllerState,
  temperature_c : Double,
  awaiting : String?,
) -> String? {
  if awaiting is Some(_) {
    // A command is still unconfirmed. Recomputing now would stack absolute
    // targets, so wait for feedback first; the device reports for every
    // command and every query, so this cannot park forever.
    @demo.log_line(
      "{\"role\":\"controller\",\"event\":\"waiting_for_feedback\",\"temperature_c\":\{temperature_c},\"desired\":\"\{state_text(state.desired_on)}\"}",
    )
    return awaiting
  }
  match @demo.relay_step(state.reported, temperature_c) {
    @demo.SetOn => {
      state.desired_on = true
      send_target(client, topics, state, true, "temperature")
    }
    @demo.SetOff => {
      state.desired_on = false
      send_target(client, topics, state, false, "temperature")
    }
    @demo.QueryDevice => {
      // The target is unambiguous, but the current relay state is unknown: ask
      // the device first and decide after the answer arrives.
      @demo.log_line(
        "{\"role\":\"controller\",\"event\":\"uncertain\",\"temperature_c\":\{temperature_c},\"desired\":null,\"result\":\"none\",\"reported\":null}",
      )
      query_device(client, topics, state)
      None
    }
    @demo.Hold => {
      @demo.log_line(
        "{\"role\":\"controller\",\"event\":\"hold\",\"temperature_c\":\{temperature_c},\"desired\":\"\{state_text(state.desired_on)}\",\"result\":\"none\",\"reported\":\{option_bool_json(state.reported)}}",
      )
      None
    }
  }
}

///|
async fn send_target(
  client : @mqtt.Client,
  topics : @demo.TopicLayout,
  state : ControllerState,
  target_on : Bool,
  reason : String,
) -> String? {
  let command_id = state.next_command_id()
  let payload = @demo.encode_command({
    command_id,
    target_on,
    reason,
  })
  let outcome = publish_checked(client, topics.command, payload)
  state.last_result = outcome
  state.last_command_id = Some(command_id)
  match outcome {
    Unknown => {
      // The broker may have delivered the command even though the PUBACK was
      // lost. Do not resend blindly and do not claim success: ask the device.
      @demo.log_line(
        "{\"role\":\"controller\",\"event\":\"command_unknown\",\"command_id\":\"\{command_id}\",\"desired\":\"\{state_text(target_on)}\",\"result\":\"unknown\",\"reported\":\{option_bool_json(state.reported)}}",
      )
      query_device(client, topics, state)
      None
    }
    NotSent => {
      // Nothing reached the broker. The desired target is still pending and the
      // next temperature sample or reconnect recomputes it.
      @demo.log_line(
        "{\"role\":\"controller\",\"event\":\"command_not_sent\",\"command_id\":\"\{command_id}\",\"desired\":\"\{state_text(target_on)}\",\"result\":\"not_sent\",\"reported\":\{option_bool_json(state.reported)}}",
      )
      query_device(client, topics, state)
      None
    }
    Sent => {
      state.log(", \"event\":\"command_sent\"")
      // The publish was acknowledged, but execution is *not* proven yet: the
      // controller waits for feedback that carries this command id.
      Some(command_id)
    }
  }
}

///|
async fn query_device(
  client : @mqtt.Client,
  topics : @demo.TopicLayout,
  state : ControllerState,
) -> Unit {
  let query_id = state.next_query_id()
  let outcome = publish_checked(client, topics.query, @demo.encode_query(query_id))
  state.last_query_id = Some(query_id)
  @demo.log_line(
    "{\"role\":\"controller\",\"event\":\"query_sent\",\"query_id\":\"\{query_id}\",\"result\":\"\{outcome_text(outcome)}\",\"desired\":\{option_bool_json(Some(state.desired_on))},\"reported\":\{option_bool_json(state.reported)}}",
  )
}

///|
/// Correlate and apply one feedback message, then repair the device when the
/// desired target and the reported state disagree.
///
/// Returns the command id still awaiting confirmation, or `None` when this
/// answer resolved it.
async fn apply_feedback(
  client : @mqtt.Client,
  topics : @demo.TopicLayout,
  state : ControllerState,
  feedback : @demo.DeviceFeedback,
  awaiting : String?,
) -> String? {
  let matches_query = match (feedback.query_id, state.last_query_id) {
    (Some(feedback_id), Some(our_id)) => feedback_id == our_id
    _ => false
  }
  let matches_awaiting = match (feedback.command_id, awaiting) {
    (Some(feedback_id), Some(our_id)) => feedback_id == our_id
    _ => false
  }
  if !matches_query && !matches_awaiting {
    // A retained report from an earlier device run, or an answer to a query
    // this generation did not send. That is evidence the device exists, but not
    // evidence of the *current* state, so `reported` stays untouched.
    @demo.log_line(
      "{\"role\":\"controller\",\"event\":\"feedback_ignored\",\"startup_id\":\"\{feedback.startup_id}\",\"sequence\":\{feedback.sequence},\"command_id\":\{option_json(feedback.command_id)},\"query_id\":\{option_json(feedback.query_id)}}",
    )
    return awaiting
  }
  state.reported = Some(feedback.state_on)
  // A correlated report is the only thing that can resolve an unknown outcome:
  // the PUBACK may have been lost even though the device executed the command.
  if state.last_result is Unknown {
    state.last_result = Sent
  }
  @demo.log_line(
    "{\"role\":\"controller\",\"event\":\"feedback\",\"startup_id\":\"\{feedback.startup_id}\",\"sequence\":\{feedback.sequence},\"state\":\"\{state_text(feedback.state_on)}\",\"command_id\":\{option_json(feedback.command_id)},\"query_id\":\{option_json(feedback.query_id)},\"desired\":\"\{state_text(state.desired_on)}\",\"reported\":\"\{state_text(feedback.state_on)}\"}",
  )
  if matches_awaiting {
    @demo.log_line(
      "{\"role\":\"controller\",\"event\":\"command_confirmed\",\"command_id\":\{option_json(feedback.command_id)},\"state\":\"\{state_text(feedback.state_on)}\",\"desired\":\"\{state_text(state.desired_on)}\",\"result\":\"confirmed\"}",
    )
  }
  if feedback.state_on != state.desired_on {
    // The device does not match the desired target: send the latest absolute
    // target instead of trusting the acknowledgement.
    let next = send_target(client, topics, state, state.desired_on, "reconcile")
    return next
  }
  None
}

///|
async fn run(config : @demo.DemoConfig, topics : @demo.TopicLayout) -> Unit {
  let state : ControllerState = {
    desired_on: false,
    reported: None,
    last_result: NotSent,
    last_command_id: None,
    last_query_id: None,
    command_counter: 0,
    query_counter: 0,
  }
  @mqtt.with_client(config.mqtt_config(), async fn(client) {
    let granted = client.subscribe([
      { topic: topics.temperature, qos: @mqtt.AtLeastOnce },
      { topic: topics.feedback, qos: @mqtt.AtLeastOnce },
    ])
    @demo.log_line(
      "{\"role\":\"controller\",\"event\":\"subscribed\",\"results\":\{@debug.to_string(granted)}}",
    )
    for result in granted {
      if result is @mqtt.Rejected {
        @demo.log_line("{\"role\":\"controller\",\"event\":\"subscription_rejected\"}")
        return
      }
    }
    // `awaiting` is the command whose outcome has not been confirmed by device
    // feedback yet. While it is set the controller reports *no* success: the
    // only proof of execution is a correlated feedback message.
    let mut awaiting : String? = None
    while true {
      let event = client.next_event()
      match event {
        @mqtt.Connected(generation) => {
          // Startup and reconnect are the same case: no local state is
          // trustworthy until a fresh answer arrives, so always ask.
          state.reported = None
          awaiting = None
          @demo.log_line(
            "{\"role\":\"controller\",\"event\":\"connected\",\"generation\":\{generation}}",
          )
          query_device(client, topics, state)
        }
        @mqtt.Disconnected(generation, reason) => {
          @demo.log_line(
            "{\"role\":\"controller\",\"event\":\"disconnected\",\"generation\":\{generation},\"reason\":\{@contracts.json_encode_string(reason)}}",
          )
          // The command may or may not have reached the device. Keep the
          // outcome unknown until the reconnect query returns fresh state.
          match awaiting {
            Some(command_id) =>
              @demo.log_line(
                "{\"role\":\"controller\",\"event\":\"command_unconfirmed\",\"command_id\":\"\{command_id}\",\"result\":\"unknown\",\"reported\":\{option_bool_json(state.reported)}}",
              )
            None => ()
          }
          awaiting = None
        }
        @mqtt.MessageReceived(message) =>
          if message.topic == topics.temperature {
            match decode_text(message.payload).bind(@demo.decode_temperature) {
              Some(temperature) =>
                awaiting = handle_temperature(
                  client,
                  topics,
                  state,
                  temperature,
                  awaiting,
                )
              None =>
                @demo.log_line(
                  "{\"role\":\"controller\",\"event\":\"invalid_temperature\",\"payload\":\{@contracts.json_encode_string(decode_text(message.payload).unwrap_or(""))}}",
                )
            }
          } else if message.topic == topics.feedback {
            match decode_text(message.payload).bind(@demo.decode_feedback) {
              Some(feedback) =>
                awaiting = apply_feedback(
                  client,
                  topics,
                  state,
                  feedback,
                  awaiting,
                )
              None =>
                @demo.log_line(
                  "{\"role\":\"controller\",\"event\":\"invalid_feedback\",\"payload\":\{@contracts.json_encode_string(decode_text(message.payload).unwrap_or(""))}}",
                )
            }
          }
      }
    }
  })
}

///|
fn decode_text(payload : Bytes) -> String? {
  Some(@utf8.decode(payload)) catch {
    _ => None
  }
}

///|
async fn main {
  let config = @demo.DemoConfig::from_env(
    "moon-demo-controller",
    "MQTT_DEMO_CONTROLLER_ID",
  )
  let topics = @demo.TopicLayout::new(config.topic_prefix)
  @demo.log_line(
    "{\"role\":\"controller\",\"event\":\"start\",\"broker\":\"\{config.host}:\{config.port}\",\"prefix\":\"\{config.topic_prefix}\"}",
  )
  // Repeated `with_client` cycles: a terminal error ends the controller with a
  // non-zero status instead of retrying forever.
  let mut cycles = 0
  while cycles < 20 {
    cycles += 1
    try {
      run(config, topics)
      return
    } catch {
      error =>
        if @async.is_cancellation_error(error) {
          // The process is being stopped (or the scope is shutting down); this
          // is an orderly exit, not a fault to report.
          return
        } else {
          @demo.log_line(
            "{\"role\":\"controller\",\"event\":\"cycle_failed\",\"error\":\{@contracts.json_encode_string(error.to_string())}}",
          )
          @async.sleep(500)
        }
    }
  }
  abort("controller gave up after 20 client cycles")
}
