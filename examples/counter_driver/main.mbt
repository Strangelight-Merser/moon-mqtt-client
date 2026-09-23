///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn log_stats(client : @mqtt.Client) -> Unit {
  let stats = client.stats()
  log("stats \{stats.generation} \{stats.reconnects} \{stats.disconnects}")
}

///|
async fn main {
  let protocol = @env.get_env_var("COUNTER_PROTOCOL").unwrap_or("311")
  let cfg = @mqtt.Config::new(
    "127.0.0.1",
    "counter-contract",
    port=@string.parse_int(@env.get_env_var("COUNTER_PORT").unwrap()),
    protocol=if protocol == "5" { @mqtt.Mqtt5 } else { @mqtt.Mqtt311 },
    reconnect_attempts=3,
    reconnect_delay_ms=1,
    max_reconnect_delay_ms=2,
    keep_alive_secs=0,
  )
  try {
    @async.with_timeout(10000, async fn() {
      @mqtt.with_client(cfg, async fn(client) {
        let mut ready = 0
        while ready < 2 {
          let event = try {
            client.next_event()
          } catch {
            @mqtt.ServerDisconnected(reason) => {
              log("terminal ServerDisconnected \{reason.code}")
              log_stats(client)
              return
            }
            @mqtt.ProtocolError(_) => {
              log("terminal ProtocolError")
              log_stats(client)
              return
            }
            @mqtt.Closed => {
              log("terminal Closed")
              log_stats(client)
              return
            }
            error => raise error
          }
          match event {
            @mqtt.Connected(generation) => {
              ready += 1
              log("connected \{generation}")
              if ready == 1 && @env.get_env_var("COUNTER_SUBSCRIBE") is Some(_) {
                ignore(client.subscribe([{ topic: "counter/topic", qos: @mqtt.AtMostOnce }]))
                log("subscribed")
              }
            }
            @mqtt.Disconnected(generation, _) =>
              log("disconnected \{generation} \{client.stats().disconnects}")
            _ => ()
          }
        }
        log_stats(client)
      })
    })
  } catch {
    @mqtt.BrokerRejected(reason) => log("initial BrokerRejected \{reason.code}")
    @mqtt.ConnectionRefused(_) => log("initial ConnectionRefused")
    error => raise error
  }
}
