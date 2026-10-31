///|
fn setting(key : String, fallback : String) -> String {
  @env.get_env_var(key).unwrap_or(fallback)
}

///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn main {
  let mqtt5 = setting("POLICY_PROTOCOL", "5") == "5"
  let ca = setting("POLICY_CA", "")
  let config = @mqtt.Config::new(
    if ca == "" { "127.0.0.1" } else { "localhost" },
    "dial-policy-baseline",
    port=@string.parse_int(setting("POLICY_PORT", "0")),
    protocol=if mqtt5 { @mqtt.Mqtt5 } else { @mqtt.Mqtt311 },
    tls=if ca == "" { @mqtt.Plain } else { @mqtt.CustomCA(ca) },
    server_disconnect_policy=if mqtt5 {
      @mqtt.RetryServerBusyOrShutdown
    } else { @mqtt.StopOnServerDisconnect },
    reconnect_attempts=2,
    reconnect_delay_ms=1,
    max_reconnect_delay_ms=2,
    connect_timeout_ms=2000,
    keep_alive_secs=0,
  )
  try @async.with_timeout(12000, async fn() {
    @mqtt.with_client(config, async fn(client) {
      let target = @string.parse_int(setting("POLICY_TARGET", "2"))
      let mut count = 0
      while count < target {
        match client.next_event() {
          @mqtt.Connected(generation) => {
            count += 1
            log("connected \{generation}")
          }
          _ => ()
        }
      }
    })
  }) catch {
    @mqtt.BrokerRejected(reason) => log("terminal BrokerRejected \{reason.code}")
    @mqtt.ConnectionRefused(_) => log("terminal ConnectionRefused")
    @mqtt.TlsFailure(_) => log("terminal TlsFailure")
    @mqtt.ReconnectExhausted(_) => log("terminal ReconnectExhausted")
    error => raise error
  }
}
