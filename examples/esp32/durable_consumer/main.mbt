///|
fn required(name : String) -> String raise {
  @env.get_env_var("B2_" + name).unwrap_or_else(() => fail("missing B2_" + name))
}

///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn main {
  let id = required("ID")
  let cfg = @mqtt.Config::new(
    required("HOST"),
    required("CLIENT_ID"),
    port=@string.from_str(required("PORT")),
    session_policy=@mqtt.ResumeSession,
    protocol=if @env.get_env_var("B2_MQTT5").unwrap_or("0") == "1" {
      @mqtt.Mqtt5
    } else {
      @mqtt.Mqtt311
    },
    session_expiry_secs=if @env.get_env_var("B2_MQTT5").unwrap_or("0") == "1" {
      300L
    } else {
      0L
    },
    tls=match @env.get_env_var("B2_CA") {
      Some(path) => @mqtt.CustomCA(path)
      None => @mqtt.Plain
    },
    username=@env.get_env_var("B2_USERNAME"),
    password=@env.get_env_var("B2_PASSWORD").map(value => @utf8.encode(value)),
    client_identity=match
      (@env.get_env_var("B2_CERT"), @env.get_env_var("B2_KEY")) {
      (Some(certificate_chain_file), Some(private_key_file)) =>
        Some({ certificate_chain_file, private_key_file, })
      (None, None) => None
      _ => fail("B2_CERT and B2_KEY must be set together")
    },
    max_inflight=4,
    send_capacity=4,
    operation_timeout_ms=10000,
    reconnect_attempts=10,
    reconnect_delay_ms=100,
    max_reconnect_delay_ms=1000,
  )
  let options = @mqtt.DurableOutboxOptions::new(required("DB"), max_records=4)
  @mqtt.with_durable_client(options, cfg, async fn(client, recovered) {
    let handle = if recovered.is_empty() {
      let envelope = @utf8.encode(required("ENVELOPE"))
      guard @relay.decode_command(envelope, @relay.now_ms()) is Some(command) &&
        command.id == id else {
        fail(
          "B2_ENVELOPE must use B2_ID and an immutable future expiry within 30 seconds",
        )
      }
      client.submit_durable_delivery(
        id,
        required("PREFIX") + "/device/set",
        envelope,
        command.expires_at_ms,
      )
    } else {
      if recovered.length() != 1 || recovered[0].id() != id {
        fail("outbox identities differ from the selected command")
      }
      log(
        "{\"event\":\"recovered\",\"id\":\{@relay.text(id)},\"packet_id\":\{recovered[0].status().packet_id}}",
      )
      recovered[0]
    }
    log(
      "{\"event\":\"admitted\",\"id\":\{@relay.text(id)},\"packet_id\":\{handle.status().packet_id}}",
    )
    let terminal = handle.wait()
    let outcome = match terminal.phase {
      @mqtt.Acknowledged => "broker_acknowledged"
      @mqtt.TerminalNotSent(_) => "not_sent"
      @mqtt.TerminalOutcomeUnknown(_) => "unknown"
      _ => fail("unexpected nonterminal durable outcome")
    }
    log(
      "{\"event\":\"delivery\",\"id\":\{@relay.text(id)},\"outcome\":\{@relay.text(outcome)},\"execution\":\"requires_independent_observation\",\"packet_id\":\{terminal.packet_id},\"attempts\":\{terminal.attempts},\"cause\":\{@relay.text(@debug.to_string(terminal.terminal_cause))}}",
    )
  })
}
