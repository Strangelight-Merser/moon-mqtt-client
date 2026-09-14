///|
fn read_temperature(payload : Bytes) -> Double? {
  let text = @utf8.decode(payload) catch { _ => return None }
  let value = @json.parse(text) catch { _ => return None }
  match value {
    Object(object) =>
      match object.get("temperature_c") {
        Some(Number(temperature, ..)) => Some(temperature)
        _ => None
      }
    _ => None
  }
}

///|
async fn main {
  let config = @mqtt.Config::new(
    @env.get_env_var("MQTT_TEST_HOST").unwrap_or("127.0.0.1"),
    "moon-temperature-controller",
    port=@string.from_str(@env.get_env_var("MQTT_TEST_PORT").unwrap_or("1883")),
    will=Some({
      topic: "demo/thermostat/availability",
      payload: @utf8.encode("offline"),
      qos: @mqtt.AtLeastOnce,
      retain: true,
    }),
  )
  @mqtt.with_client(config, async fn(client) {
    client.wait_connected()
    client.publish(
      "demo/thermostat/availability",
      @utf8.encode("online"),
      qos=@mqtt.AtLeastOnce,
      retain=true,
    )
    let results = client.subscribe([
      { topic: "demo/thermostat/temperature", qos: @mqtt.AtLeastOnce, },
    ])
    match results {
      [@mqtt.Granted(_)] => println("temperature subscription granted")
      [@mqtt.Rejected] => {
        println("temperature subscription rejected")
        return
      }
      _ => {
        println("unexpected SUBACK result count")
        return
      }
    }
    let mut relay_on = false
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) =>
          if message.topic == "demo/thermostat/temperature" {
            match read_temperature(message.payload) {
              Some(temperature) => {
                let result = @contracts.thermostat_step(relay_on, temperature)
                match result.command {
                  @contracts.TurnOn => {
                    relay_on = true
                    client.publish(
                      "demo/thermostat/relay/set",
                      @utf8.encode("ON"),
                      qos=@mqtt.AtLeastOnce,
                    )
                    client.publish(
                      "demo/thermostat/relay/state",
                      @utf8.encode("ON"),
                      qos=@mqtt.AtLeastOnce,
                      retain=true,
                    )
                    println("temperature=\{temperature}: relay ON")
                  }
                  @contracts.TurnOff => {
                    relay_on = false
                    client.publish(
                      "demo/thermostat/relay/set",
                      @utf8.encode("OFF"),
                      qos=@mqtt.AtLeastOnce,
                    )
                    client.publish(
                      "demo/thermostat/relay/state",
                      @utf8.encode("OFF"),
                      qos=@mqtt.AtLeastOnce,
                      retain=true,
                    )
                    println("temperature=\{temperature}: relay OFF")
                  }
                  @contracts.NoChange =>
                    println("temperature=\{temperature}: hold \{result.state}")
                }
              }
              None => println("ignored invalid temperature JSON")
            }
          }
        @mqtt.Connected(generation) => {
          client.publish(
            "demo/thermostat/availability",
            @utf8.encode("online"),
            qos=@mqtt.AtLeastOnce,
            retain=true,
          )
          println("connected generation \{generation}")
        }
        @mqtt.Disconnected(generation, reason) =>
          println("disconnected generation \{generation}: \{reason}")
      }
    }
  })
}
