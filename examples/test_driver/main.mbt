///|
/// Write one JSON diagnostics line through the unbuffered stdout writer.
///
/// `println` uses the C stdio block buffer, which hides driver events from the
/// parent test harness when stdout is a pipe. The harness reads these lines as
/// they are produced, so each line is written directly.
async fn log_line(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
fn required_env(name : String) -> String raise {
  match @env.get_env_var(name) {
    Some(value) => value
    None => fail("missing environment variable \{name}")
  }
}

///|
fn config(
  tls? : Bool = false,
  will? : @mqtt.Will? = None,
  keep_alive_secs? : Int = 30,
) -> @mqtt.Config raise {
  let host = required_env("MQTT_TEST_HOST")
  let port : Int = @string.from_str(required_env("MQTT_TEST_PORT"))
  let operation_timeout_ms : Int = @string.from_str(
    @env.get_env_var("MQTT_TEST_OPERATION_TIMEOUT_MS").unwrap_or(
      @env.get_env_var("MQTT_TEST_ACK_TIMEOUT_MS").unwrap_or("2000"),
    ),
  )
  let connect_timeout_ms : Int = @string.from_str(
    @env.get_env_var("MQTT_TEST_CONNECT_TIMEOUT_MS").unwrap_or("5000"),
  )
  let use_tls = tls || @env.get_env_var("MQTT_TEST_CA") is Some(_)
  @mqtt.Config::new(
    host,
    @env.get_env_var("MQTT_TEST_CLIENT_ID").unwrap_or(
      "moon-integration-\{required_env("MQTT_TEST_SCENARIO")}",
    ),
    port~,
    tls=if use_tls {
      @mqtt.CustomCA(required_env("MQTT_TEST_CA"))
    } else {
      @mqtt.Plain
    },
    client_identity=match (
      @env.get_env_var("MQTT_TEST_CERT"),
      @env.get_env_var("MQTT_TEST_KEY"),
    ) {
      (Some(certificate_chain_file), Some(private_key_file)) =>
        Some({ certificate_chain_file, private_key_file })
      (None, None) => None
      _ => fail("MQTT_TEST_CERT and MQTT_TEST_KEY must be provided together")
    },
    username=@env.get_env_var("MQTT_TEST_USERNAME"),
    password=match @env.get_env_var("MQTT_TEST_PASSWORD") {
      Some(value) => Some(@utf8.encode(value))
      None => None
    },
    will~,
    keep_alive_secs~,
    connect_timeout_ms~,
    operation_timeout_ms~,
    reconnect_delay_ms=100,
    max_reconnect_delay_ms=500,
    reconnect_attempts=20,
  )
}

///|
async fn auth_acl_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    match client.next_event() {
      @mqtt.Connected(_) => ()
      event => fail("expected authenticated connection, got \{@debug.to_string(event)}")
    }
    let results = client.subscribe([
      { topic: "allowed/read", qos: @mqtt.AtLeastOnce, },
      { topic: "denied/read", qos: @mqtt.AtLeastOnce, },
    ])
    // Mosquitto deliberately acknowledges syntactically valid subscriptions
    // even when its ACL suppresses delivery. The Python oracle verifies the
    // denied topic remains silent after this readiness event.
    guard results == [
      @mqtt.Granted(@mqtt.AtLeastOnce),
      @mqtt.Granted(@mqtt.AtLeastOnce),
    ] else {
      fail("unexpected authenticated ACL SUBACK \{@debug.to_string(results)}")
    }
    client.publish(
      "allowed/write",
      b"authenticated",
      qos=@mqtt.AtLeastOnce,
    )
    log_line("{\"event\":\"auth_acl_ready\"}")
    match @async.with_timeout_opt(700, () => client.next_event()) {
      None => log_line("{\"event\":\"acl_denied_delivery\"}")
      Some(event) => fail("ACL-denied message arrived: \{@debug.to_string(event)}")
    }
    client.disconnect()
  })
}

///|
async fn suback_rejection_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    match client.next_event() {
      @mqtt.Connected(_) => ()
      event => fail("expected connection, got \{@debug.to_string(event)}")
    }
    let results = client.subscribe([
      { topic: "allowed/read", qos: @mqtt.AtLeastOnce, },
      { topic: "denied/read", qos: @mqtt.AtLeastOnce, },
    ])
    guard results == [@mqtt.Granted(@mqtt.AtLeastOnce), @mqtt.Rejected] else {
      fail("broker did not return the expected partial SUBACK: \{@debug.to_string(results)}")
    }
    log_line("{\"event\":\"suback_rejection_verified\"}")
    client.disconnect()
  })
}

///|
async fn connected_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    log_line("{\"event\":\"connected\"}")
    client.disconnect()
  })
}

///|
async fn tcp_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    let results = client.subscribe([
      { topic: "it/to-moon/qos0", qos: @mqtt.AtMostOnce, },
      { topic: "it/to-moon/qos1", qos: @mqtt.AtLeastOnce, },
    ])
    guard results ==
      [@mqtt.Granted(@mqtt.AtMostOnce), @mqtt.Granted(@mqtt.AtLeastOnce)] else {
      fail("unexpected SUBACK \{@debug.to_string(results)}")
    }
    log_line("{\"event\":\"subscribed\"}")
    client.publish("it/from-moon/qos0", b"moon-qos0")
    client.publish("it/from-moon/qos1", b"moon-qos1", qos=@mqtt.AtLeastOnce)
    let mut qos0 = false
    let mut qos1 = false
    while !qos0 || !qos1 {
      match client.next_event() {
        @mqtt.MessageReceived(message) if message.topic == "it/to-moon/qos0" &&
          message.payload == b"paho-qos0" &&
          message.qos == @mqtt.AtMostOnce => {
          qos0 = true
          log_line("{\"event\":\"received_qos0\"}")
        }
        @mqtt.MessageReceived(message) if message.topic == "it/to-moon/qos1" &&
          message.payload == b"paho-qos1" &&
          message.qos == @mqtt.AtLeastOnce => {
          qos1 = true
          log_line("{\"event\":\"received_qos1\"}")
        }
        @mqtt.Connected(_) => ()
        event => fail("unexpected TCP event \{@debug.to_string(event)}")
      }
    }
    client.disconnect()
  })
}

///|
async fn tls_case() -> Unit {
  @mqtt.with_client(config(tls=true), async fn(client) {
    log_line("{\"event\":\"connected\"}")
    client.disconnect()
  })
}

///|
async fn mtls_repeat_case() -> Unit {
  for _ in 0..<30 {
    @mqtt.with_client(config(tls=true), async fn(client) {
      client.publish("it/mtls-repeat", b"ok", qos=@mqtt.AtLeastOnce)
    })
  }
  log_line("{\"event\":\"mtls_repeat_done\"}")
}

///|
async fn mtls_case() -> Unit {
  @mqtt.with_client(config(tls=true), async fn(client) {
    let results = client.subscribe([
      { topic: "it/to-moon/mtls", qos: @mqtt.AtLeastOnce, },
    ])
    guard results == [@mqtt.Granted(@mqtt.AtLeastOnce)] else {
      fail("unexpected SUBACK \{@debug.to_string(results)}")
    }
    log_line("{\"event\":\"subscribed\"}")
    client.publish("it/from-moon/mtls", b"moon-mtls", qos=@mqtt.AtLeastOnce)
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) if message.topic == "it/to-moon/mtls" &&
          message.payload == b"paho-mtls" &&
          message.qos == @mqtt.AtLeastOnce => {
          log_line("{\"event\":\"received_mtls\"}")
          break
        }
        @mqtt.Connected(_) => ()
        event => fail("unexpected mTLS event \{@debug.to_string(event)}")
      }
    }
    client.disconnect()
  })
}

///|
async fn reconnect_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    ignore(
      client.subscribe([{ topic: "it/reconnect", qos: @mqtt.AtLeastOnce, }]),
    )
    let mut reconnected = false
    while true {
      match client.next_event() {
        @mqtt.Connected(generation) => {
          log_line("{\"event\":\"connected\",\"generation\":\{generation}}")
          if generation > 1 {
            reconnected = true
          }
        }
        @mqtt.Disconnected(generation, _) =>
          log_line("{\"event\":\"disconnected\",\"generation\":\{generation}}")
        @mqtt.MessageReceived(message) if reconnected &&
          message.topic == "it/reconnect" &&
          message.payload == b"after-restart" => {
          let snapshot = client.stats()
          guard snapshot.generation >= 2 else {
            fail("reconnect must advance the generation")
          }
          guard snapshot.reconnects >= 1 else {
            fail("stats must count successful reconnects")
          }
          guard snapshot.disconnects >= 1 else {
            fail("stats must count disconnects")
          }
          guard snapshot.last_disconnect is Some(_) else {
            fail("stats must record the last disconnect reason")
          }
          log_line(
            "{\"event\":\"message_after_reconnect\",\"generation\":\{snapshot.generation},\"reconnects\":\{snapshot.reconnects},\"disconnects\":\{snapshot.disconnects},\"pending\":\{snapshot.pending_requests}}",
          )
          client.disconnect()
          return
        }
        event => fail("unexpected reconnect event \{@debug.to_string(event)}")
      }
    }
  })
}

///|
async fn lost_puback_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    try {
      client.publish(
        "it/lost-puback",
        b"must-not-succeed",
        qos=@mqtt.AtLeastOnce,
      )
      log_line("{\"event\":\"publish_outcome\",\"outcome\":\"success\"}")
      fail("QoS1 publish returned success without PUBACK")
    } catch {
      @mqtt.OutcomeUnknown(_) =>
        log_line("{\"event\":\"publish_outcome\",\"outcome\":\"unknown\"}")
      error =>
        log_line(
          "{\"event\":\"publish_outcome\",\"outcome\":\"error\",\"detail\":\"\{error}\"}",
        )
    }
  })
}

///|
async fn will_case(abrupt : Bool) -> Unit {
  @mqtt.with_client(
    config(
      will=Some({
        topic: "it/will",
        payload: b"abrupt",
        qos: @mqtt.AtLeastOnce,
        retain: false,
      }),
    ),
    async fn(client) {
      log_line("{\"event\":\"connected\"}")
      if abrupt {
        fail("intentional abrupt callback failure")
      }
      client.disconnect()
    },
  ) catch {
    error => if !abrupt { raise error }
  }
}

///|
async fn heartbeat_case() -> Unit {
  @mqtt.with_client(config(keep_alive_secs=1), async fn(client) {
    while true {
      match client.next_event() {
        @mqtt.Connected(generation) =>
          log_line("{\"event\":\"connected\",\"generation\":\{generation}}")
        @mqtt.Disconnected(generation, _) => {
          log_line("{\"event\":\"disconnected\",\"generation\":\{generation}}")
          client.disconnect()
          return
        }
        _ => ()
      }
    }
  })
}

///|
async fn retained_case(clear : Bool) -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    client.publish(
      "it/retained",
      if clear {
        b""
      } else {
        b"retained-value"
      },
      qos=@mqtt.AtLeastOnce,
      retain=true,
    )
    log_line(
      if clear {
        "{\"event\":\"retained_cleared\"}"
      } else {
        "{\"event\":\"retained_set\"}"
      },
    )
    client.disconnect()
  })
}

///|
async fn unsubscribe_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    match client.next_event() {
      @mqtt.Connected(_) => ()
      event =>
        fail("expected initial Connected, got \{@debug.to_string(event)}")
    }
    ignore(
      client.subscribe([{ topic: "it/unsubscribe", qos: @mqtt.AtLeastOnce, }]),
    )
    client.unsubscribe(["it/unsubscribe"])
    // Returning from unsubscribe proves that the protocol UNSUBACK was read.
    log_line("{\"event\":\"unsubscribed\"}")
    let event = @async.with_timeout_opt(700, () => client.next_event())
    match event {
      None => log_line("{\"event\":\"no_message_after_unsubscribe\"}")
      Some(event) => fail("event after unsubscribe: \{@debug.to_string(event)}")
    }
    client.disconnect()
  })
}

///|
/// Exercise the read-only diagnostics surface and print one machine-readable
/// snapshot. The harness asserts on specific counters, so a wrong generation or
/// a leaking queue depth is visible without a monitoring service.
async fn stats_case() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    match client.next_event() {
      @mqtt.Connected(_) => ()
      event => fail("expected initial Connected, got \{@debug.to_string(event)}")
    }
    // Subscribe and publish on separate topics: a message delivered back to
    // this client would queue a control PUBACK and make the snapshot depend on
    // scheduling instead of on the counter under test.
    ignore(client.subscribe([{ topic: "it/stats-in", qos: @mqtt.AtLeastOnce, }]))
    client.publish("it/stats-out", b"stats", qos=@mqtt.AtLeastOnce)
    let snapshot = client.stats()
    guard snapshot.generation == 1 else {
      fail("expected generation 1, got \{snapshot.generation}")
    }
    guard snapshot.connected else { fail("stats must report a live connection") }
    guard snapshot.pending_requests == 0 else {
      fail("completed requests must not stay pending")
    }
    guard !(snapshot.last_disconnect is Some(_)) else {
      fail("no disconnect has happened yet")
    }
    log_line(
      "{\"event\":\"stats\",\"generation\":\{snapshot.generation},\"pending\":\{snapshot.pending_requests},\"business\":\{snapshot.business_queue_depth},\"control\":\{snapshot.control_queue_depth},\"reconnects\":\{snapshot.reconnects},\"disconnects\":\{snapshot.disconnects},\"unknown\":\{snapshot.unknown_outcomes}}",
    )
    client.disconnect()
  })
}

fn client_error_type(error : Error) -> String {
  try {
    raise error
  } catch {
    @mqtt.InvalidConfig(_) => "InvalidConfig"
    @mqtt.TlsFailure(_) => "TlsFailure"
    @mqtt.Closed => "Closed"
    @mqtt.ConnectionRefused(_) => "ConnectionRefused"
    @mqtt.ProtocolError(_) => "ProtocolError"
    @mqtt.ReconnectExhausted(_) => "ReconnectExhausted"
    @mqtt.NotConnected => "NotConnected"
    @mqtt.Backpressure(_) => "Backpressure"
    _ => "Other"
  }
}

///|
fn identity_config(client_id : String, cert : String, key : String) -> @mqtt.Config raise {
  let host = required_env("MQTT_TEST_HOST")
  let port : Int = @string.from_str(required_env("MQTT_TEST_PORT"))
  @mqtt.Config::new(
    host,
    client_id,
    port~,
    tls=@mqtt.CustomCA(required_env("MQTT_TEST_CA")),
    client_identity=Some({
      certificate_chain_file: cert,
      private_key_file: key,
    }),
    connect_timeout_ms=@string.from_str(
      @env.get_env_var("MQTT_TEST_CONNECT_TIMEOUT_MS").unwrap_or("5000"),
    ),
    operation_timeout_ms=2000,
    reconnect_delay_ms=100,
    max_reconnect_delay_ms=500,
    reconnect_attempts=20,
  )
}

///|
async fn mtls_dual_case() -> Unit {
  @mqtt.with_client(
    identity_config(
      "device-a",
      required_env("MQTT_TEST_CERT_A"),
      required_env("MQTT_TEST_KEY_A"),
    ),
    async fn(alice) {
      @mqtt.with_client(
        identity_config(
          "device-b",
          required_env("MQTT_TEST_CERT_B"),
          required_env("MQTT_TEST_KEY_B"),
        ),
        async fn(bob) {
          ignore(
            alice.subscribe([
              { topic: "it/dual/a", qos: @mqtt.AtLeastOnce, },
            ]),
          )
          ignore(
            bob.subscribe([
              { topic: "it/dual/b", qos: @mqtt.AtLeastOnce, },
            ]),
          )
          log_line("{\"event\":\"dual_subscribed\"}")
          alice.publish("it/dual/b", b"from-a", qos=@mqtt.AtLeastOnce)
          bob.publish("it/dual/a", b"from-b", qos=@mqtt.AtLeastOnce)
          let mut got_a = false
          while !got_a {
            match alice.next_event() {
              @mqtt.MessageReceived(message) if message.topic == "it/dual/a" &&
                message.payload == b"from-b" => {
                got_a = true
              }
              @mqtt.Connected(_) => ()
              event => fail("alice unexpected \{@debug.to_string(event)}")
            }
          }
          let mut got_b = false
          while !got_b {
            match bob.next_event() {
              @mqtt.MessageReceived(message) if message.topic == "it/dual/b" &&
                message.payload == b"from-a" => {
                got_b = true
              }
              @mqtt.Connected(_) => ()
              event => fail("bob unexpected \{@debug.to_string(event)}")
            }
          }
          log_line("{\"event\":\"dual_exchanged\"}")
          bob.disconnect()
          alice.disconnect()
        },
      )
    },
  )
}

///|
async fn mtls_acl_listener_case() -> Unit {
  @mqtt.with_client(config(tls=true), async fn(client) {
    let topic = required_env("MQTT_TEST_SUB_TOPIC")
    ignore(client.subscribe([{ topic, qos: @mqtt.AtLeastOnce, }]))
    client.publish(
      required_env("MQTT_TEST_PUB_TOPIC"),
      @utf8.encode(required_env("MQTT_TEST_PAYLOAD")),
      qos=@mqtt.AtLeastOnce,
    )
    let expected = @utf8.encode(required_env("MQTT_TEST_PAYLOAD"))
    let mut own = false
    while !own {
      match client.next_event() {
        @mqtt.MessageReceived(message) if message.payload == expected => {
          own = true
        }
        @mqtt.Connected(_) => ()
        event => fail("unexpected ACL listener event \{@debug.to_string(event)}")
      }
    }
    log_line("{\"event\":\"acl_own_received\"}")
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) => {
          log_line("{\"event\":\"acl_foreign\"}")
          fail("ACL leaked message: \{@debug.to_string(message)}")
        }
        @mqtt.Connected(_) => ()
        @mqtt.Disconnected(_, reason) =>
          fail("ACL listener disconnected: \{reason}")
      }
    }
  })
}

///|
async fn mtls_acl_intruder_case() -> Unit {
  @mqtt.with_client(config(tls=true), async fn(client) {
    client.publish(
      required_env("MQTT_TEST_PUB_TOPIC"),
      @utf8.encode(required_env("MQTT_TEST_PAYLOAD")),
      qos=@mqtt.AtLeastOnce,
    )
    log_line("{\"event\":\"acl_intruder_published\"}")
    client.disconnect()
  })
}

///|
async fn main {
  @async.with_timeout(20000, () => {
    try {
      match required_env("MQTT_TEST_SCENARIO") {
        "tcp" => tcp_case()
        "tls" => tls_case()
        "tls_reject" => tls_case()
        "mtls" => mtls_case()
        "mtls_repeat" => mtls_repeat_case()
        "mtls_dual" => mtls_dual_case()
        "mtls_acl_listener" => mtls_acl_listener_case()
        "mtls_acl_intruder" => mtls_acl_intruder_case()
        "reconnect" => reconnect_case()
        "lost_puback" => lost_puback_case()
        "fragment" => connected_case()
        "will_disconnect" => will_case(false)
        "will_abrupt" => will_case(true)
        "heartbeat" => heartbeat_case()
        "retained_set" => retained_case(false)
        "retained_clear" => retained_case(true)
        "unsubscribe" => unsubscribe_case()
        "stats" => stats_case()
        "auth_acl" => auth_acl_case()
        "suback_rejection" => suback_rejection_case()
        scenario => fail("unknown MQTT_TEST_SCENARIO \{scenario}")
      }
    } catch {
      error => {
        let tag = client_error_type(error)
        if tag == "Other" {
          raise error
        } else {
          log_line("{\"event\":\"client_error\",\"type\":\"\{tag}\"}")
          fail("client_error \{tag}")
        }
      }
    }
  })
}
