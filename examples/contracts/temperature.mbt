///|
/// A command emitted by the thermostat. `NoChange` is deliberate hysteresis,
/// not a missing output.
pub(all) enum RelayCommand {
  TurnOn
  TurnOff
  NoChange
} derive(Debug, Eq)

///|
/// Result of one temperature sample. The caller publishes `state` as a
/// retained payload only when `command` changes the relay.
pub(all) struct ThermostatResult {
  command : RelayCommand
  state : String
} derive(Debug, Eq)

///|
/// Apply the 28 C on / 26 C off hysteresis contract.
pub fn thermostat_step(
  relay_on : Bool,
  temperature_c : Double,
) -> ThermostatResult {
  if !relay_on && temperature_c >= 28.0 {
    { command: TurnOn, state: "ON", }
  } else if relay_on && temperature_c <= 26.0 {
    { command: TurnOff, state: "OFF", }
  } else {
    { command: NoChange, state: if relay_on { "ON" } else { "OFF" }, }
  }
}

///|
/// Availability is a retained MQTT state, independent from relay state.
pub fn availability_payload(online : Bool) -> String {
  if online {
    "online"
  } else {
    "offline"
  }
}
