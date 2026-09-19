///|
async fn main {
  let prefix = @relay.prefix()
  let settings = @demo.DemoConfig::from_env("moon-ha-simulator", "HA_RELAY_DEVICE_CLIENT_ID")
  let base = settings.mqtt_config()
  let config = { ..base, will: Some({ topic: prefix + "/device/availability", payload: b"offline", qos: @mqtt.AtLeastOnce, retain: true }) }
  let boot_id = @relay.fresh_id()
  let mut state = false
  let mut sequence = 0
  let recent : Array[(@relay.Command)] = []
  @mqtt.with_client(config, async fn(client) {
    let results = client.subscribe([
      { topic: prefix + "/device/set", qos: @mqtt.AtLeastOnce },
      { topic: prefix + "/device/query", qos: @mqtt.AtLeastOnce },
    ])
    if results.contains(@mqtt.Rejected) { raise @mqtt.ProtocolError("relay subscription rejected") }
    while true {
      match client.next_event() {
        @mqtt.Connected(_) => client.publish(prefix + "/device/availability", b"online", qos=@mqtt.AtLeastOnce, retain=true)
        @mqtt.Disconnected(_, _) => ()
        @mqtt.MessageReceived(message) => {
          if message.retain { continue }
          let correlation : String? = if message.topic == prefix + "/device/set" {
            match @relay.decode_command(message.payload, @relay.now_ms()) {
              Some(command) => {
                let mut duplicate : @relay.Command? = None
                for old in recent {
                  if old.id == command.id { duplicate = Some(old); break }
                }
                match duplicate {
                  Some(old) if old != command => {
                    @demo.log_line("{\"event\":\"conflicting_command_id\"}")
                    continue
                  }
                  Some(_) => ()
                  None => {
                    // Bounded recent IDs plus an immutable short expiry. This
                    // simulates an absolute relay target, never physical GPIO.
                    state = command.target
                    if recent.length() == 64 { ignore(recent.remove(0)) }
                    recent.push(command)
                    @demo.log_line("{\"event\":\"applied\",\"id\":\{@relay.text(command.id)},\"state\":\{@relay.text(@relay.state_text(state))}}")
                  }
                }
                Some(command.id)
              }
              None => {
                @demo.log_line("{\"event\":\"rejected_command\"}")
                None
              }
            }
          } else {
            @relay.object(message.payload).bind(fields => @relay.field(fields, "id"))
          }
          if correlation is Some(correlation_id) {
            sequence += 1
            let feedback : @relay.Feedback = { boot_id, sequence, state, correlation_id }
            client.publish(prefix + "/device/feedback", @utf8.encode(feedback.encode()), qos=@mqtt.AtLeastOnce, retain=true)
          }
        }
      }
    }
  })
}
