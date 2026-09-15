///|
pub(all) enum PrimitiveTelemetry {
  BoolValue(Bool)
  IntValue(Int)
  DoubleValue(Double)
  TextValue(String)
} derive(Debug, Eq)

///|
pub(all) struct VelocityCommand {
  command_id : String
  linear_x : Double
  angular_z : Double
} derive(Debug, Eq)

///|
/// Encode primitive telemetry. Booleans, integers, finite doubles and strings
/// are each a valid JSON document; non-finite doubles become `null` (see
/// `json_encode_number`).
pub fn primitive_payload(value : PrimitiveTelemetry) -> String {
  match value {
    BoolValue(v) => if v { "true" } else { "false" }
    IntValue(v) => v.to_string()
    DoubleValue(v) => json_encode_number(v)
    TextValue(v) => json_encode_string(v)
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
fn json_double_field(object : Map[String, Json], name : String) -> Double? {
  match object.get(name) {
    Some(Number(value, ..)) => Some(value)
    _ => None
  }
}

///|
/// JSON numbers are finite by definition. `1e999` parses to `+inf`, and bare
/// `NaN`/`Infinity` are not JSON at all, so every non-finite value is rejected
/// before it can reach a device.
fn finite(value : Double) -> Bool {
  let inf = 1.0 / 0.0
  // `value != value` is the portable NaN test. A NaN speed would otherwise
  // compare false against every bound and slip through a range check.
  !(value != value || value == inf || value == -inf)
}

///|
/// Decode the public MQTT JSON command contract. Missing, wrongly typed and
/// non-finite fields are rejected instead of receiving defaults, and the
/// command id must be non-empty so a receiver can correlate the command.
///
/// Velocity *ranges* are a device concern and are deliberately not enforced
/// here: a generic MQTT contract accepts any finite speed and lets the target
/// device reject values it cannot execute.
pub fn parse_velocity_command(payload : String) -> VelocityCommand? {
  let value = @json.parse(payload) catch { _ => return None }
  match value {
    Object(object) => {
      let id = json_string_field(object, "command_id")
      let linear_x = json_double_field(object, "linear_x")
      let angular_z = json_double_field(object, "angular_z")
      match (id, linear_x, angular_z) {
        (Some(id), Some(x), Some(z)) if !id.is_empty() && finite(x) && finite(z) =>
          Some({ command_id: id, linear_x: x, angular_z: z, })
        _ => None
      }
    }
    _ => None
  }
}

///|
pub fn command_receipt(command_id : String, accepted : Bool) -> String {
  let out = StringBuilder()
  json_append_object(out, [
    ("command_id", json_encode_string(command_id)),
    ("status", json_encode_string(if accepted { "accepted" } else { "rejected" })),
  ])
  out.to_string()
}

///|
/// Convert the application command envelope to the JSON representation of
/// `geometry_msgs/msg/Twist` used by a JSON-configured MQTT/ROS bridge.
pub fn twist_payload(command : VelocityCommand) -> String {
  let linear = StringBuilder()
  json_append_object(linear, [
    ("x", json_encode_number(command.linear_x)),
    ("y", "0"),
    ("z", "0"),
  ])
  let angular = StringBuilder()
  json_append_object(angular, [
    ("x", "0"),
    ("y", "0"),
    ("z", json_encode_number(command.angular_z)),
  ])
  let out = StringBuilder()
  json_append_object(out, [
    ("linear", linear.to_string()),
    ("angular", angular.to_string()),
  ])
  out.to_string()
}
