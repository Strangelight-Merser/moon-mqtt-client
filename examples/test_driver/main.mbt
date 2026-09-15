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
  @mqtt.Config::new(
    host,
    "moon-integration-\{required_env("MQTT_TEST_SCENARIO")}",
    port~,
    tls=if tls {
      @mqtt.CustomCA(required_env("MQTT_TEST_CA"))
    } else {
      @mqtt.Plain
    },
    will~,
    keep_alive_secs~,
    operation_timeout_ms~,
    reconnect_delay_ms=100,
    max_reconnect_delay_ms=500,
    reconnect_attempts=20,
  )
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

///|
async fn main {
  @async.with_timeout(20000, () => {
    match required_env("MQTT_TEST_SCENARIO") {
      "tcp" => tcp_case()
      "tls" => tls_case()
      "tls_reject" => tls_case()
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
      scenario => fail("unknown MQTT_TEST_SCENARIO \{scenario}")
    }
  })
}
