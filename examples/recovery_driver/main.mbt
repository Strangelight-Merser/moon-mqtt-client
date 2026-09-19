///|
fn required(name : String) -> String raise {
  @env.get_env_var(name).unwrap_or_else(() => fail("missing \{name}"))
}

///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn log_terminal_cause(cause : @mqtt.ClientError?) -> Unit {
  match cause {
    Some(@mqtt.BrokerSessionLost(_)) => log("cause=BrokerSessionLost")
    Some(@mqtt.DeliveryAttemptsExhausted(_)) =>
      log("cause=DeliveryAttemptsExhausted")
    Some(@mqtt.ReconnectExhausted(_)) => log("cause=ReconnectExhausted")
    Some(@mqtt.ProtocolError(_)) => log("cause=ProtocolError")
    Some(@mqtt.Closed) => log("cause=Closed")
    Some(_) => log("cause=OtherClientError")
    None => fail("terminal delivery had no typed cause")
  }
}

///|
async fn main {
  let mode = @env.get_env_var("Q1_MODE").unwrap_or("recover")
  let config = @mqtt.Config::new(
    "127.0.0.1",
    @env.get_env_var("Q1_CLIENT_ID").unwrap_or("moon-q1-recovery"),
    port=@string.from_str(required("Q1_PORT")),
    session_policy=@mqtt.ResumeSession,
    operation_timeout_ms=@string.from_str(
      @env.get_env_var("Q1_ACK_TIMEOUT_MS").unwrap_or("500"),
    ),
    write_timeout_ms=500,
    reconnect_delay_ms=20,
    max_reconnect_delay_ms=50,
    reconnect_attempts=@string.from_str(
      @env.get_env_var("Q1_RECONNECT_ATTEMPTS").unwrap_or("3"),
    ),
    max_inflight=@string.from_str(
      @env.get_env_var("Q1_MAX_INFLIGHT").unwrap_or("1"),
    ),
    send_capacity=@string.from_str(
      @env.get_env_var("Q1_SEND_CAPACITY").unwrap_or("1"),
    ),
  )
  @mqtt.with_client(config, async fn(client) {
    if @env.get_env_var("Q1_SUBSCRIBE").unwrap_or("0") == "1" {
      let results = client.subscribe([
        { topic: "q1/events", qos: @mqtt.AtLeastOnce, },
      ])
      guard results == [@mqtt.Granted(@mqtt.AtLeastOnce)] else {
        fail("recovery test subscription was rejected")
      }
      log("subscribed")
    }
    let handle = client.submit_delivery(
      @env.get_env_var("Q1_DELIVERY_ID").unwrap_or("delivery-1"),
      @env.get_env_var("Q1_TOPIC").unwrap_or("q1/recover"),
      @utf8.encode(@env.get_env_var("Q1_PAYLOAD").unwrap_or("payload")),
    )
    let admitted = handle.status()
    log(
      "admitted id=\{admitted.delivery_id} packet=\{admitted.packet_id} attempts=\{admitted.attempts}",
    )
    if mode == "cancel_wait" {
      let first = @async.with_timeout_opt(25, () => handle.wait())
      guard first is None else { fail("first handle wait unexpectedly completed") }
      log("wait_cancelled")
    }
    let terminal = handle.wait()
    match terminal.phase {
      @mqtt.Acknowledged =>
        log(
          "acknowledged id=\{terminal.delivery_id} packet=\{terminal.packet_id} attempts=\{terminal.attempts}",
        )
      @mqtt.TerminalNotSent(reason) => {
        log("not_sent reason=\{reason}")
        log_terminal_cause(terminal.terminal_cause)
      }
      @mqtt.TerminalOutcomeUnknown(reason) => {
        log("unknown reason=\{reason}")
        log_terminal_cause(terminal.terminal_cause)
      }
      _ => fail("wait returned a nonterminal phase")
    }
  })
}
