///|
/// New HA relay consumer protocol; independent of the thermostat demo.
/// Expiry is mandatory. Neither broker acknowledgement nor a retained report
/// establishes the device's current physical state.
pub(all) struct Command {
  id : String
  target : Bool
  expires_at_ms : Int64
} derive(Eq, Debug)

///|
pub(all) struct Feedback {
  boot_id : String
  sequence : Int
  state : Bool
  correlation_id : String
} derive(Eq, Debug)

///|
pub fn now_ms() -> Int64 {
  @env.now().reinterpret_as_int64()
}

///|
pub fn fresh_id() -> String raise @mqtt.ClientError {
  match @env.rand(16) {
    Some(bytes) if bytes.length() == 16 => @hex.encode(bytes[:])
    _ => raise @mqtt.InvalidConfig("native entropy unavailable for command identity")
  }
}

///|
pub fn text(value : String) -> String {
  @contracts.json_encode_string(value)
}

///|
pub fn state_text(value : Bool) -> String {
  if value { "ON" } else { "OFF" }
}

///|
pub fn object(payload : Bytes) -> Map[String, Json]? {
  let decoded = @utf8.decode(payload) catch { _ => return None }
  let parsed = @json.parse(decoded) catch { _ => return None }
  match parsed {
    Object(fields) => Some(fields)
    _ => None
  }
}

///|
pub fn field(fields : Map[String, Json], key : String) -> String? {
  match fields.get(key) {
    Some(String(value)) if value.length() > 0 && value.length() <= 128 => Some(value)
    _ => None
  }
}

///|
fn integer(fields : Map[String, Json], key : String) -> Int64? {
  match fields.get(key) {
    Some(Number(value, ..)) if value >= 0.0 && value <= 9007199254740991.0 => {
      let integer = value.to_int64()
      if integer.to_double() == value { Some(integer) } else { None }
    }
    _ => None
  }
}

///|
pub fn Command::encode(self : Command) -> String {
  "{\"id\":\{text(self.id)},\"target\":\{text(state_text(self.target))},\"expires_at_ms\":\{self.expires_at_ms}}"
}

///|
pub fn decode_command(payload : Bytes, now : Int64) -> Command? {
  guard object(payload) is Some(fields) else { return None }
  match (field(fields, "id"), field(fields, "target"), integer(fields, "expires_at_ms")) {
    (Some(id), Some(target), Some(expires_at_ms)) if expires_at_ms > now && expires_at_ms - now <= 30000L =>
      match target {
        "ON" => Some({ id, target: true, expires_at_ms })
        "OFF" => Some({ id, target: false, expires_at_ms })
        _ => None
      }
    _ => None
  }
}

///|
pub fn Feedback::encode(self : Feedback) -> String {
  "{\"boot_id\":\{text(self.boot_id)},\"sequence\":\{self.sequence},\"state\":\{text(state_text(self.state))},\"correlation_id\":\{text(self.correlation_id)}}"
}

///|
pub fn decode_feedback(payload : Bytes) -> Feedback? {
  guard object(payload) is Some(fields) else { return None }
  match (field(fields, "boot_id"), integer(fields, "sequence"), field(fields, "state"), field(fields, "correlation_id")) {
    (Some(boot_id), Some(sequence), Some(state), Some(correlation_id)) if sequence > 0L && sequence <= 2147483647L =>
      match state {
        "ON" => Some({ boot_id, sequence: sequence.to_int(), state: true, correlation_id })
        "OFF" => Some({ boot_id, sequence: sequence.to_int(), state: false, correlation_id })
        _ => None
      }
    _ => None
  }
}

///|
pub fn query(id : String) -> String {
  "{\"id\":\{text(id)}}"
}

///|
pub fn prefix() -> String raise @mqtt.ClientError {
  let value = @env.get_env_var("HA_RELAY_PREFIX").unwrap_or("moon/ha/relay")
  if value.is_empty() || value.length() > 128 || value.contains("+") || value.contains("#") || value.contains("\u0000") {
    raise @mqtt.InvalidConfig("invalid HA_RELAY_PREFIX")
  }
  value
}

///|
pub fn discovery(prefix : String, entity_id : String) -> String {
  "{\"name\":\"Moon relay\",\"unique_id\":\{text(entity_id)},\"command_topic\":\{text(prefix + "/ha/set")},\"state_topic\":\{text(prefix + "/ha/state")},\"availability\":[{\"topic\":\{text(prefix + "/controller/availability")}},{\"topic\":\{text(prefix + "/device/availability")}}],\"availability_mode\":\"all\",\"optimistic\":false,\"qos\":1,\"retain\":false,\"payload_on\":\"ON\",\"payload_off\":\"OFF\"}"
}
