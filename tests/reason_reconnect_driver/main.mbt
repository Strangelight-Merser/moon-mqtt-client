///|
fn required(name : String) -> String raise {
  @env.get_env_var(name).unwrap_or_else(() => fail("missing \{name}"))
}

///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn main {
  let delivery_mode = @env.get_env_var("B3_DELIVERY").unwrap_or("0") == "1"
  let policy = match @env.get_env_var("B3_POLICY").unwrap_or("terminal") {
    "terminal" => @mqtt.StopOnServerDisconnect
    "retry" => @mqtt.RetryServerBusyOrShutdown
    value => fail("unsupported policy \{value}")
  }
  let target_connections : Int = @string.from_str(
    @env.get_env_var("B3_TARGET_CONNECTIONS").unwrap_or("999"),
  )
  let config = @mqtt.Config::new(
    "127.0.0.1",
    "moon-reason-reconnect",
    port=@string.from_str(required("B3_PORT")),
    protocol=@mqtt.Mqtt5,
    session_policy=if delivery_mode {
      @mqtt.ResumeSession
    } else {
      @mqtt.CleanSession
    },
    session_expiry_secs=if delivery_mode { 60L } else { 0L },
    server_disconnect_policy=policy,
    reconnect_attempts=@string.from_str(
      @env.get_env_var("B3_RETRY_BUDGET").unwrap_or("2"),
    ),
    reconnect_delay_ms=1,
    max_reconnect_delay_ms=2,
    keep_alive_secs=0,
  )
  @mqtt.with_client(config, async fn(client) {
    if delivery_mode {
      let handle = client.submit_delivery(
        "reason-reconnect-delivery",
        "b3/recover",
        b"retained-work",
      )
      let status = handle.wait()
      match status.phase {
        @mqtt.Acknowledged =>
          log(
            "delivery acknowledged packet=\{status.packet_id} attempts=\{status.attempts}",
          )
        @mqtt.TerminalNotSent(reason) =>
          log("delivery not_sent reason=\{reason}")
        @mqtt.TerminalOutcomeUnknown(reason) =>
          log("delivery unknown reason=\{reason}")
        _ => fail("delivery wait returned nonterminal phase")
      }
      return
    }
    let mut connected = 0
    while true {
      try {
        match client.next_event() {
          @mqtt.Connected(generation) => {
            connected += 1
            log("connected generation=\{generation}")
            if connected >= target_connections {
              return
            }
          }
          @mqtt.Disconnected(generation, reason) =>
            log("disconnected generation=\{generation} reason=\{reason}")
          @mqtt.MessageReceived(_) => ()
        }
      } catch {
        @mqtt.ServerDisconnected(reason) => {
          log("terminal server_disconnect code=\{reason.code}")
          return
        }
        @mqtt.BrokerRejected(reason) => {
          log("terminal broker_rejected code=\{reason.code}")
          return
        }
        error => raise error
      }
    }
  })
}
