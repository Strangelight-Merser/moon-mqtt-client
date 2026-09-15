///|
test "ROS bridge handles primitive telemetry" {
  assert_eq(primitive_payload(BoolValue(true)), "true")
  assert_eq(primitive_payload(BoolValue(false)), "false")
  assert_eq(primitive_payload(IntValue(42)), "42")
  assert_eq(primitive_payload(DoubleValue(0.5)), "0.5")
  assert_eq(primitive_payload(TextValue("base_link")), "\"base_link\"")
}

///|
/// Parse or abort the test: a payload produced by the contract must be JSON.
fn parsed(payload : String) -> Json {
  @json.parse(payload) catch { _ => abort("\{payload} is not JSON") }
}

///|
test "telemetry encoding is valid JSON for every variant" {
  for payload in [
    primitive_payload(BoolValue(true)),
    primitive_payload(IntValue(42)),
    primitive_payload(DoubleValue(0.5)),
    primitive_payload(TextValue("base_link")),
  ] {
    ignore(parsed(payload))
  }
}

///|
test "non-finite telemetry becomes JSON null instead of Infinity or NaN" {
  let inf = 1.0 / 0.0
  let nan = 0.0 / 0.0
  assert_eq(primitive_payload(DoubleValue(inf)), "null")
  assert_eq(primitive_payload(DoubleValue(-inf)), "null")
  assert_eq(primitive_payload(DoubleValue(nan)), "null")
  for payload in [
    primitive_payload(DoubleValue(inf)),
    primitive_payload(DoubleValue(nan)),
  ] {
    // `null` is valid JSON, unlike the bare Infinity/NaN tokens.
    guard parsed(payload) is Json::Null else {
      abort("\{payload} must parse as JSON null")
    }
  }
}

///|
test "text telemetry escapes quotes, backslashes and control characters" {
  assert_eq(primitive_payload(TextValue("a\"b")), "\"a\\\"b\"")
  assert_eq(primitive_payload(TextValue("a\\b")), "\"a\\\\b\"")
  assert_eq(primitive_payload(TextValue("a\nb")), "\"a\\nb\"")
  assert_eq(primitive_payload(TextValue("a\tb")), "\"a\\tb\"")
  assert_eq(primitive_payload(TextValue("a\rb")), "\"a\\rb\"")
  assert_eq(primitive_payload(TextValue("a\u{01}b")), "\"a\\u0001b\"")
  assert_eq(primitive_payload(TextValue("a\u{1F}b")), "\"a\\u001fb\"")
  let payload = primitive_payload(TextValue("quote:\" backslash:\\ ctrl:\u{07}"))
  match parsed(payload) {
    String(round_trip) =>
      assert_eq(round_trip, "quote:\" backslash:\\ ctrl:\u{07}")
    _ => abort("escaped text payload must parse back to a string: \{payload}")
  }
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

///|
test "velocities must be finite and the command id must not be empty" {
  // `1e999` overflows to +inf while staying syntactically valid JSON.
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":1e999,\"angular_z\":0}",
    ),
    None,
  )
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":-1e999,\"angular_z\":0}",
    ),
    None,
  )
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":0,\"angular_z\":1e999}",
    ),
    None,
  )
  // Bare NaN is not JSON, so the document is rejected before field checks.
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":NaN,\"angular_z\":0}",
    ),
    None,
  )
  // The internal NaN test must be self-comparison based: a bare `NaN` token is
  // already rejected by the parser, and `1e999` above covers the infinity path.
  assert_false(finite(0.0 / 0.0))
  assert_false(finite(1.0 / 0.0))
  assert_false(finite(-1.0 / 0.0))
  assert_true(finite(0.0))
  assert_true(finite(-3.5))
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"\",\"linear_x\":0.4,\"angular_z\":0.2}",
    ),
    None,
  )
  // A missing id cannot be correlated and must be rejected.
  assert_eq(parse_velocity_command("{\"linear_x\":0.4,\"angular_z\":0.2}"), None)
  // Finite values at the edges of the double range are still accepted: range
  // limits belong to the device, not to the generic contract.
  assert_eq(
    parse_velocity_command(
      "{\"command_id\":\"cmd-9\",\"linear_x\":1.0e308,\"angular_z\":-1.0e308}",
    ),
    Some({ command_id: "cmd-9", linear_x: 1.0e308, angular_z: -1.0e308, }),
  )
}

///|
test "command ids with quotes and backslashes survive the receipt round trip" {
  let id = "cmd-\"9\"\\x"
  let receipt = command_receipt(id, true)
  match parsed(receipt) {
    Object(object) =>
      match object.get("command_id") {
        Some(String(round_trip)) => assert_eq(round_trip, id)
        _ => abort("receipt lost the command id: \{receipt}")
      }
    _ => abort("receipt must be a JSON object: \{receipt}")
  }
  assert_eq(
    receipt,
    "{\"command_id\":\"cmd-\\\"9\\\"\\\\x\",\"status\":\"accepted\"}",
  )
}
