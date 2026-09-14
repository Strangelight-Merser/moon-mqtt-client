///|
test "ROS bridge handles primitive telemetry" {
  assert_eq(primitive_payload(BoolValue(true)), "true")
  assert_eq(primitive_payload(IntValue(42)), "42")
  assert_eq(primitive_payload(DoubleValue(0.5)), "0.5")
  assert_eq(primitive_payload(TextValue("base_link")), "base_link")
}

///|
test "ROS bridge validates JSON command and creates receipt" {
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":0.4,\"angular_z\":-0.2}",
    ),
    Some({ command_id: "cmd-9", linear_x: 0.4, angular_z: -0.2, }),
  )
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":\"fast\",\"angular_z\":0}",
    ),
    None,
  )
  assert_eq(
    command_receipt("cmd-9", true),
    "{\"command_id\":\"cmd-9\",\"status\":\"accepted\"}",
  )
  assert_eq(
    twist_payload({ command_id: "cmd-9", linear_x: 0.4, angular_z: -0.2, }),
    "{\"linear\":{\"x\":0.4,\"y\":0,\"z\":0},\"angular\":{\"x\":0,\"y\":0,\"z\":-0.2}}",
  )
}
