///|
/// A command emitted by the thermostat. `NoChange` is deliberate hysteresis and
/// `Uncertain` means the current device state is unknown, so neither ON nor OFF
/// may be assumed. The caller must read device feedback first.
pub(all) enum RelayCommand {
  TurnOn
  TurnOff
  NoChange
  Uncertain
} derive(Debug, Eq)

///|
/// Result of one temperature sample. The caller publishes `state` as a retained
/// payload only when `command` changes the relay.
pub(all) struct ThermostatResult {
  command : RelayCommand
  state : String
} derive(Debug, Eq)

///|
/// Apply the 28 C on / 26 C off hysteresis contract.
///
/// `relay_on` is `None` until the controller has received device feedback. An
/// unknown state inside the deadband is `Uncertain` rather than a guess: the
/// controller must query the device instead of inventing an ON/OFF answer.
/// Outside the deadband the temperature itself decides the target, so the
/// command is unambiguous even before the first feedback arrives.
pub fn thermostat_step(
  relay_on : Bool?,
  temperature_c : Double,
) -> ThermostatResult {
  let on = match relay_on {
    Some(value) => value
    None =>
      if temperature_c >= 28.0 {
        return { command: TurnOn, state: "ON" }
      } else if temperature_c <= 26.0 {
        return { command: TurnOff, state: "OFF" }
      } else {
        return { command: Uncertain, state: "UNKNOWN" }
      }
  }
  if !on && temperature_c >= 28.0 {
    { command: TurnOn, state: "ON" }
  } else if on && temperature_c <= 26.0 {
    { command: TurnOff, state: "OFF" }
  } else {
    { command: NoChange, state: if on { "ON" } else { "OFF" } }
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
