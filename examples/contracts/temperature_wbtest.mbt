///|
test "thermostat turns on at 28 and off at 26" {
  assert_eq(thermostat_step(Some(false), 27.9).command, NoChange)
  assert_eq(thermostat_step(Some(false), 28.0).command, TurnOn)
  assert_eq(thermostat_step(Some(true), 26.1).command, NoChange)
  assert_eq(thermostat_step(Some(true), 26.0).command, TurnOff)
}

///|
test "thermostat holds state inside deadband and reports availability" {
  assert_eq(thermostat_step(Some(false), 27.0).state, "OFF")
  assert_eq(thermostat_step(Some(true), 27.0).state, "ON")
  assert_eq(availability_payload(true), "online")
  assert_eq(availability_payload(false), "offline")
}

///|
test "unknown relay state inside the deadband is uncertain, never a guess" {
  // Regression for the old `relay_on = false` default: a 27 C sample must not
  // be reported as a successful OFF.
  let middle = thermostat_step(None, 27.0)
  assert_eq(middle.command, Uncertain)
  assert_eq(middle.state, "UNKNOWN")
  assert_false(middle.command is NoChange)
  assert_false(middle.command is TurnOff)
}

///|
test "unknown relay state outside the deadband still yields an absolute target" {
  assert_eq(thermostat_step(None, 28.0).command, TurnOn)
  assert_eq(thermostat_step(None, 29.5).command, TurnOn)
  assert_eq(thermostat_step(None, 26.0).command, TurnOff)
  assert_eq(thermostat_step(None, 24.0).command, TurnOff)
  assert_eq(thermostat_step(None, 24.0).state, "OFF")
  // An unknown state at the exact deadband edges is decided by the threshold,
  // matching the known-state behaviour.
  assert_eq(thermostat_step(Some(false), 28.0).command, TurnOn)
  assert_eq(thermostat_step(Some(true), 26.0).command, TurnOff)
}
