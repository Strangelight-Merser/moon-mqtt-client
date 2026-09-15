///|
/// Connection settings and topic layout for the thermostat state-sync demo.
///
/// Both the controller and the simulated device load this from the same
/// environment variables and default to the same local broker, so two separate
/// processes agree on the wire contract without sharing code paths. Credentials
/// are read from the environment and never printed.
pub(all) struct DemoConfig {
  host : String
  port : Int
  client_id : String
  username : String?
  password : String?
  tls : Bool
  ca_file : String?
  topic_prefix : String
  /// Optional QoS 1 acknowledgement fault point for the controller: the first
  /// `publish` call is abandoned before its PUBACK arrives.
  drop_first_puback : Bool
}

///|
pub fn DemoConfig::from_env(
  default_client_id : String,
  client_id_env : String,
) -> DemoConfig {
  let host = env_or("MQTT_DEMO_HOST", "127.0.0.1")
  let port_text = env_or("MQTT_DEMO_PORT", "1883")
  let port = @string.parse_int(port_text) catch { _ => 1883 }
  let topic_prefix = env_or("MQTT_DEMO_TOPIC_PREFIX", "moon/demo/thermostat")
  {
    host,
    port,
    client_id: env_or(client_id_env, default_client_id),
    username: @env.get_env_var("MQTT_DEMO_USERNAME"),
    password: @env.get_env_var("MQTT_DEMO_PASSWORD"),
    tls: env_or("MQTT_DEMO_TLS", "0") == "1",
    ca_file: @env.get_env_var("MQTT_DEMO_CA_FILE"),
    topic_prefix,
    drop_first_puback: env_or("MQTT_DEMO_DROP_FIRST_PUBACK", "0") == "1",
  }
}

///|
fn env_or(name : String, fallback : String) -> String {
  match @env.get_env_var(name) {
    Some(value) if !value.is_empty() => value
    _ => fallback
  }
}

///|
pub fn DemoConfig::mqtt_config(self : DemoConfig) -> @mqtt.Config {
  @mqtt.Config::new(
    self.host,
    self.client_id,
    port=self.port,
    tls=if self.tls {
      match self.ca_file {
        Some(path) => @mqtt.CustomCA(path)
        None => @mqtt.SystemRoots
      }
    } else {
      @mqtt.Plain
    },
    username=self.username,
    password=match self.password {
      Some(text) => Some(@utf8.encode(text))
      None => None
    },
    keep_alive_secs=10,
    connect_timeout_ms=4000,
    // The controller must be able to notice a lost acknowledgement quickly, but
    // the budget still covers queueing, the write and the wait.
    operation_timeout_ms=2000,
    write_timeout_ms=2000,
    ping_response_timeout_ms=2000,
    reconnect_delay_ms=200,
    max_reconnect_delay_ms=1000,
    reconnect_attempts=10,
  )
}

///|
/// Topic layout. Temperature and commands are separate from feedback and
/// queries, and device feedback is the only retained state: the controller never
/// publishes a retained "success" claim.
pub(all) struct TopicLayout {
  temperature : String
  command : String
  feedback : String
  query : String
  availability : String
}

///|
pub fn TopicLayout::new(prefix : String) -> TopicLayout {
  {
    temperature: prefix + "/temperature",
    command: prefix + "/relay/set",
    feedback: prefix + "/relay/feedback",
    query: prefix + "/relay/query",
    availability: prefix + "/device/availability",
  }
}

///|
/// Absolute relay target carried by a command. Commands are idempotent: the same
/// id with the same target may be replayed safely.
pub(all) struct RelayCommand {
  command_id : String
  target_on : Bool
  reason : String
} derive(Debug, Eq)

///|
/// Device report. `startup_id` identifies the device process, `sequence`
/// increases monotonically inside that process, and exactly one of the two
/// correlation ids is set so the controller can tell an answer to its own query
/// from a retained message left over by an earlier connection.
pub(all) struct DeviceFeedback {
  startup_id : String
  sequence : Int
  state_on : Bool
  command_id : String?
  query_id : String?
} derive(Debug, Eq)

///|
/// What the controller should do after one temperature sample, given the last
/// known relay state (`None` until device feedback arrives).
pub(all) enum RelayDecision {
  SetOn
  SetOff
  Hold
  QueryDevice
} derive(Debug, Eq)

///|
/// Map the shared hysteresis contract onto a controller decision. The thresholds
/// (28 C on, 26 C off, deadband hold) live in `examples/contracts`; this
/// function only translates `Uncertain` into an explicit device query instead
/// of guessing a relay state.
pub fn relay_step(relay_on : Bool?, temperature_c : Double) -> RelayDecision {
  match @contracts.thermostat_step(relay_on, temperature_c).command {
    @contracts.TurnOn => SetOn
    @contracts.TurnOff => SetOff
    @contracts.NoChange => Hold
    @contracts.Uncertain => QueryDevice
  }
}

///|
pub fn encode_temperature(temperature_c : Double) -> String {
  let out = StringBuilder()
  @contracts.json_append_object(out, [
    ("temperature_c", @contracts.json_encode_number(temperature_c)),
  ])
  out.to_string()
}

///|
pub fn decode_temperature(payload : String) -> Double? {
  match json_object(payload) {
    Some(object) =>
      match object.get("temperature_c") {
        Some(Number(value, ..)) if finite(value) => Some(value)
        _ => None
      }
    None => None
  }
}

///|
pub fn encode_command(command : RelayCommand) -> String {
  let out = StringBuilder()
  @contracts.json_append_object(out, [
    ("command_id", @contracts.json_encode_string(command.command_id)),
    ("target", @contracts.json_encode_string(if command.target_on { "ON" } else { "OFF" })),
    ("reason", @contracts.json_encode_string(command.reason)),
  ])
  out.to_string()
}

///|
pub fn decode_command(payload : String) -> RelayCommand? {
  match json_object(payload) {
    Some(object) => {
      let id = json_string_field(object, "command_id")
      let target = json_string_field(object, "target")
      let reason = json_string_field(object, "reason").unwrap_or("")
      match (id, target) {
        (Some(command_id), Some(target)) if !command_id.is_empty() && valid_reason(reason) =>
          match target {
            "ON" => Some({ command_id, target_on: true, reason })
            "OFF" => Some({ command_id, target_on: false, reason })
            _ => None
          }
        _ => None
      }
    }
    None => None
  }
}

///|
/// Accepted command reasons. An unknown reason is rejected rather than applied,
/// so a malformed envelope cannot silently move the relay.
fn valid_reason(reason : String) -> Bool {
  match reason {
    "initial" | "temperature" | "reconcile" | "query" | "reconnect" => true
    _ => false
  }
}

///|
pub fn encode_query(query_id : String) -> String {
  let out = StringBuilder()
  @contracts.json_append_object(out, [("query_id", @contracts.json_encode_string(query_id))])
  out.to_string()
}

///|
pub fn decode_query(payload : String) -> String? {
  match json_object(payload) {
    Some(object) =>
      match json_string_field(object, "query_id") {
        Some(id) if !id.is_empty() => Some(id)
        _ => None
      }
    None => None
  }
}

///|
pub fn encode_feedback(feedback : DeviceFeedback) -> String {
  let out = StringBuilder()
  @contracts.json_append_object(out, [
    ("startup_id", @contracts.json_encode_string(feedback.startup_id)),
    ("sequence", feedback.sequence.to_string()),
    (
      "state",
      @contracts.json_encode_string(if feedback.state_on { "ON" } else { "OFF" }),
    ),
    (
      "command_id",
      match feedback.command_id {
        Some(id) => @contracts.json_encode_string(id)
        None => "null"
      },
    ),
    (
      "query_id",
      match feedback.query_id {
        Some(id) => @contracts.json_encode_string(id)
        None => "null"
      },
    ),
  ])
  out.to_string()
}

///|
pub fn decode_feedback(payload : String) -> DeviceFeedback? {
  match json_object(payload) {
    Some(object) => {
      let startup_id = json_string_field(object, "startup_id")
      let sequence = match object.get("sequence") {
        Some(Number(value, ..)) => Some(value.to_int())
        _ => None
      }
      let state = json_string_field(object, "state")
      let command_id = json_optional_string_field(object, "command_id")
      let query_id = json_optional_string_field(object, "query_id")
      match (startup_id, sequence, state) {
        (Some(startup_id), Some(sequence), Some(state)) if !startup_id.is_empty() &&
          sequence > 0 =>
          match state {
            "ON" =>
              Some({
                startup_id,
                sequence,
                state_on: true,
                command_id,
                query_id,
              })
            "OFF" =>
              Some({
                startup_id,
                sequence,
                state_on: false,
                command_id,
                query_id,
              })
            _ => None
          }
        _ => None
      }
    }
    None => None
  }
}

///|
fn json_object(payload : String) -> Map[String, Json]? {
  let value = @json.parse(payload) catch { _ => return None }
  match value {
    Object(object) => Some(object)
    _ => None
  }
}

///|
fn json_string_field(object : Map[String, Json], name : String) -> String? {
  match object.get(name) {
    Some(String(value)) => Some(value)
    _ => None
  }
}

///|
/// `null` and a missing key both decode to `None`; any other type is invalid.
fn json_optional_string_field(
  object : Map[String, Json],
  name : String,
) -> String? {
  match object.get(name) {
    Some(String(value)) if !value.is_empty() => Some(value)
    _ => None
  }
}

///|
/// Non-finite doubles have no JSON number form. The demo uses the same
/// self-comparison NaN test as the contract layer.
pub fn finite(value : Double) -> Bool {
  let inf = 1.0 / 0.0
  !(value != value || value == inf || value == -inf)
}
