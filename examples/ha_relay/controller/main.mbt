///|
async fn publish(client : @mqtt.Client, topic : String, payload : String, retain? : Bool = false) -> Unit {
  client.publish(topic, @utf8.encode(payload), qos=@mqtt.AtLeastOnce, retain~)
}

///|
async fn announce(client : @mqtt.Client, prefix : String, entity : String) -> Unit {
  publish(client, "homeassistant/switch/" + entity + "/config", @relay.discovery(prefix, entity), retain=true)
}

/// Retained controller metadata is bounded to one value per topic.  A broker
/// disconnect may happen after the write starts, so transient failures leave
/// the value dirty and let the next connected loop retry the idempotent
/// metadata publication.  Physical commands never use this state.
priv struct DirtyMetadata {
  mut discovery : Bool
  mut availability : String?
  mut state : String?
}

///|
async fn flush_metadata(
  client : @mqtt.Client,
  prefix : String,
  entity : String,
  dirty : DirtyMetadata,
) -> Unit {
  if dirty.discovery {
    try {
      announce(client, prefix, entity)
      dirty.discovery = false
    } catch {
      @mqtt.NotConnected | @mqtt.NotSent(_) | @mqtt.OutcomeUnknown(_) | @mqtt.Backpressure(_) => return
      error => raise error
    }
  }
  // A fresh reported state must be visible before advertising controller
  // availability=online.  Offline is safe to publish first because it only
  // removes the controller from the available set while state is reconciled.
  if dirty.availability == Some("offline") {
    match dirty.availability {
      Some(value) => try {
        publish(client, prefix + "/controller/availability", value, retain=true)
        dirty.availability = None
      } catch {
        @mqtt.NotConnected | @mqtt.NotSent(_) | @mqtt.OutcomeUnknown(_) | @mqtt.Backpressure(_) => return
        error => raise error
      }
      None => ()
    }
  }
  match dirty.state {
    Some(value) => try {
      publish(client, prefix + "/ha/state", value, retain=true)
      dirty.state = None
    } catch {
      @mqtt.NotConnected | @mqtt.NotSent(_) | @mqtt.OutcomeUnknown(_) | @mqtt.Backpressure(_) => return
      error => raise error
    }
    None => ()
  }
  match dirty.availability {
    Some(value) => try {
      publish(client, prefix + "/controller/availability", value, retain=true)
      dirty.availability = None
    } catch {
      @mqtt.NotConnected | @mqtt.NotSent(_) | @mqtt.OutcomeUnknown(_) | @mqtt.Backpressure(_) => return
      error => raise error
    }
    None => ()
  }
}

///|
async fn main {
  let prefix = @relay.prefix()
  let entity = @env.get_env_var("HA_RELAY_ENTITY_ID").unwrap_or("moon_relay")
  if entity.is_empty() || entity.length() > 64 ||
    !entity.iter().all(c => (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_') {
    raise @mqtt.InvalidConfig("HA_RELAY_ENTITY_ID must contain lowercase ASCII letters, digits or underscore")
  }
  let settings = @demo.DemoConfig::from_env("moon-ha-controller", "HA_RELAY_CONTROLLER_CLIENT_ID")
  let base = settings.mqtt_config()
  let config = { ..base, will: Some({ topic: prefix + "/controller/availability", payload: b"offline", qos: @mqtt.AtLeastOnce, retain: true }) }
  @mqtt.with_client(config, async fn(client) {
    let results = client.subscribe([
      { topic: prefix + "/ha/set", qos: @mqtt.AtLeastOnce },
      { topic: prefix + "/device/feedback", qos: @mqtt.AtLeastOnce },
      { topic: prefix + "/device/availability", qos: @mqtt.AtLeastOnce },
      { topic: "homeassistant/status", qos: @mqtt.AtLeastOnce },
    ])
    if results.contains(@mqtt.Rejected) { raise @mqtt.ProtocolError("HA subscription rejected") }
    let mut pending : String? = None
    let mut query_id : String? = None
    let mut pending_until = 0L
    let mut next_query = 0L
    let mut last_boot = ""
    let mut last_sequence = 0
    let dirty = DirtyMetadata::{ discovery: true, availability: None, state: None }
    while true {
      let event = @async.with_timeout_opt(250, () => client.next_event())
      match event {
        Some(@mqtt.Connected(_)) => {
          pending = None
          query_id = None
          last_boot = ""
          last_sequence = 0
          next_query = 0L
          dirty.discovery = true
          dirty.availability = Some("offline")
          dirty.state = Some("None")
        }
        Some(@mqtt.Disconnected(_, _)) => {
          pending = None
          query_id = None
          next_query = 0L
        }
        Some(@mqtt.MessageReceived(message)) => {
          if message.topic == "homeassistant/status" && message.payload == b"online" {
            dirty.discovery = true
            next_query = 0L
          } else if message.topic == prefix + "/device/availability" {
            if message.payload == b"online" { next_query = 0L }
            else {
              dirty.availability = Some("offline")
              dirty.state = Some("None")
            }
          } else if message.topic == prefix + "/ha/set" {
            if message.retain || pending is Some(_) ||
              !(message.payload == b"ON" || message.payload == b"OFF") {
              @demo.log_line("{\"event\":\"command_rejected\",\"reason\":\"retained, busy or malformed\"}")
              continue
            }
            let command : @relay.Command = { id: @relay.fresh_id(), target: message.payload == b"ON", expires_at_ms: @relay.now_ms() + 5000L }
            pending = Some(command.id)
            pending_until = command.expires_at_ms
            @demo.log_line("{\"event\":\"desired\",\"id\":\{@relay.text(command.id)},\"state\":\{@relay.text(@relay.state_text(command.target))}}")
            try {
              publish(client, prefix + "/device/set", command.encode())
              @demo.log_line("{\"event\":\"broker_accepted\",\"id\":\{@relay.text(command.id)}}")
            } catch {
              @mqtt.NotSent(_) | @mqtt.Backpressure(_) | @mqtt.NotConnected => {
                @demo.log_line("{\"event\":\"not_sent\"}")
                next_query = 0L
              }
              @mqtt.OutcomeUnknown(_) => {
                @demo.log_line("{\"event\":\"outcome_unknown\"}")
                next_query = 0L
              }
              error => raise error
            }
          } else if message.topic == prefix + "/device/feedback" {
            match @relay.decode_feedback(message.payload) {
              Some(feedback) if (pending == Some(feedback.correlation_id) || query_id == Some(feedback.correlation_id)) &&
                (feedback.boot_id != last_boot || feedback.sequence > last_sequence) => {
                last_boot = feedback.boot_id
                last_sequence = feedback.sequence
                if pending == Some(feedback.correlation_id) { pending = None }
                if query_id == Some(feedback.correlation_id) { query_id = None }
                dirty.state = Some(@relay.state_text(feedback.state))
                dirty.availability = Some("online")
                @demo.log_line("{\"event\":\"reported\",\"id\":\{@relay.text(feedback.correlation_id)},\"state\":\{@relay.text(@relay.state_text(feedback.state))}}")
              }
              _ => ()
            }
          }
        }
        None => ()
      }
      if !client.stats().connected { continue }
      let now = @relay.now_ms()
      if pending is Some(_) && now >= pending_until {
        pending = None
        next_query = 0L
        @demo.log_line("{\"event\":\"execution_unconfirmed\"}")
        dirty.availability = Some("offline")
      }
      if now >= next_query {
        if query_id is Some(_) {
          dirty.availability = Some("offline")
          dirty.state = Some("None")
        }
        let id = @relay.fresh_id()
        query_id = Some(id)
        next_query = now + 5000L
        try publish(client, prefix + "/device/query", @relay.query(id)) catch {
          @mqtt.NotConnected | @mqtt.NotSent(_) | @mqtt.OutcomeUnknown(_) | @mqtt.Backpressure(_) => ()
          error => raise error
        }
      }
      flush_metadata(client, prefix, entity, dirty)
    }
  })
}
