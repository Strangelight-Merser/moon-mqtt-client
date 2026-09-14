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
pub fn primitive_payload(value : PrimitiveTelemetry) -> String {
  match value {
    BoolValue(v) => v.to_string()
    IntValue(v) => v.to_string()
    DoubleValue(v) => v.to_string()
    TextValue(v) => v
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
/// Decode the public MQTT JSON command contract. JSON parsing excludes
/// non-finite numbers; missing and wrongly typed fields are rejected instead of
/// receiving defaults.
pub fn parse_velocity_command(payload : String) -> VelocityCommand? {
  let value = @json.parse(payload) catch { _ => return None }
  match value {
    Object(object) => {
      let id = json_string_field(object, "command_id")
      let linear_x = json_double_field(object, "linear_x")
      let angular_z = json_double_field(object, "angular_z")
      match (id, linear_x, angular_z) {
        (Some(id), Some(x), Some(z)) =>
          Some({ command_id: id, linear_x: x, angular_z: z, })
        _ => None
      }
    }
    _ => None
  }
}

///|
pub fn command_receipt(command_id : String, accepted : Bool) -> String {
  (
    {
      "command_id": command_id,
      "status": if accepted {
        "accepted"
      } else {
        "rejected"
      },
    } : Json).stringify()
}

///|
/// Convert the application command envelope to the JSON representation of
/// `geometry_msgs/msg/Twist` used by a JSON-configured MQTT/ROS bridge.
pub fn twist_payload(command : VelocityCommand) -> String {
  (
    {
      "linear": { "x": command.linear_x, "y": 0.0, "z": 0.0 },
      "angular": { "x": 0.0, "y": 0.0, "z": command.angular_z },
    } : Json).stringify()
}
