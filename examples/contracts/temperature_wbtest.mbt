///|
test "thermostat turns on at 28 and off at 26" {
  assert_eq(thermostat_step(false, 27.9).command, NoChange)
  assert_eq(thermostat_step(false, 28.0).command, TurnOn)
  assert_eq(thermostat_step(true, 26.1).command, NoChange)
  assert_eq(thermostat_step(true, 26.0).command, TurnOff)
}

///|
test "thermostat holds state inside deadband and reports availability" {
  assert_eq(thermostat_step(false, 27.0).state, "OFF")
  assert_eq(thermostat_step(true, 27.0).state, "ON")
  assert_eq(availability_payload(true), "online")
  assert_eq(availability_payload(false), "offline")
}
