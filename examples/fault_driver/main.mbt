///|
fn env(name : String) -> String raise {
  @env.get_env_var(name).unwrap_or_else(() => {
    fail("missing environment variable")
  })
}

///|
fn config(
  max_inflight? : Int = 2,
  receive_capacity? : Int = 128,
) -> @mqtt.Config raise {
  let case = env("MQTT_FAULT_CASE")
  @mqtt.Config::new(
    "127.0.0.1",
    "fault-" + case,
    port=@string.from_str(env("MQTT_FAULT_PORT")),
    operation_timeout_ms=250,
    write_timeout_ms=250,
    ping_response_timeout_ms=250,
    reconnect_delay_ms=30,
    max_reconnect_delay_ms=50,
    reconnect_attempts=3,
    max_inflight~,
    receive_capacity~,
  )
}

///|
async fn wrong_puback() -> Unit {
  let mut succeeded = false
  @mqtt.with_client(config(), async fn(client) {
    client.publish("fault/wrong-id", b"one", qos=@mqtt.AtLeastOnce)
    succeeded = true
  }) catch {
    _ => ()
  }
  if succeeded {
    fail("wrong PUBACK identifier falsely completed publish")
  }
}

///|
async fn timeout_reconnect() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    try {
      client.publish("fault/first", b"first", qos=@mqtt.AtLeastOnce)
      fail("first publish succeeded without timely PUBACK")
    } catch {
      @mqtt.OutcomeUnknown(_) => ()
      error => raise error
    }
    client.wait_connected()
    client.publish("fault/second", b"second", qos=@mqtt.AtLeastOnce)
  })
}

///|
async fn full_inflight_disconnect() -> Unit {
  @mqtt.with_client(config(max_inflight=1), async fn(client) {
    @async.with_task_group(group => {
      group.spawn_bg(allow_failure=true, () => {
        client.publish("fault/pending", b"pending", qos=@mqtt.AtLeastOnce)
      })
      @async.sleep(40)
      client.disconnect()
    })
  })
}

///|
async fn partial_suback() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    let results = client.subscribe([
      { topic: "fault/accepted", qos: @mqtt.AtMostOnce, },
      { topic: "fault/rejected", qos: @mqtt.AtLeastOnce, },
    ])
    guard results == [@mqtt.Granted(@mqtt.AtMostOnce), @mqtt.Rejected] else {
      fail("partial SUBACK result was not preserved")
    }
  })
}

///|
async fn single_suback() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    let results = client.subscribe([
      { topic: "fault/slow", qos: @mqtt.AtMostOnce, },
    ])
    guard results == [@mqtt.Granted(@mqtt.AtMostOnce)] else {
      fail("slow SUBACK result was not preserved")
    }
  })
}

///|
async fn idle_then_suback() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    @async.sleep(350)
    let results = client.subscribe([
      { topic: "fault/idle", qos: @mqtt.AtMostOnce, },
    ])
    guard results == [@mqtt.Granted(@mqtt.AtMostOnce)] else {
      fail("SUBACK after idle period was not preserved")
    }
  })
}

///|
async fn cancel_reconnect() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    @async.with_task_group(group => {
      let first = group.spawn(no_wait=true, allow_failure=true, () => {
        client.publish("fault/cancelled", b"first", qos=@mqtt.AtLeastOnce)
      })
      @async.sleep(40)
      first.cancel()
      first.wait() catch { _ => () }
      client.wait_connected()
      client.publish("fault/after-cancel", b"second", qos=@mqtt.AtLeastOnce)
    })
  })
}

///|
async fn large_incoming_publish() -> Unit {
  @mqtt.with_client(config(), async fn(client) {
    let mut received = false
    while !received {
      match client.next_event() {
        @mqtt.MessageReceived(message) => {
          guard message.topic == "fault/large" else {
            fail("large publish topic changed")
          }
          guard message.payload.length() == 32000 else {
            fail("large publish payload was truncated")
          }
          guard message.payload[0] == 0 && message.payload[31999] == 122 else {
            fail("large publish payload was corrupted")
          }
          received = true
        }
        _ => ()
      }
    }
  })
}

///|
async fn slow_consumer_overflow() -> Unit {
  let mut saw_backpressure = false
  @mqtt.with_client(config(receive_capacity=2), async fn(_) {
    // Deliberately leave Connected and all messages in the bounded queue.
    @async.sleep(1000)
  }) catch {
    @mqtt.Backpressure(_) => saw_backpressure = true
    error => raise error
  }
  if !saw_backpressure {
    fail("event overflow did not terminate with Backpressure")
  }
}

///|
async fn main {
  @async.with_timeout(5000, () => {
    match env("MQTT_FAULT_CASE") {
      "wrong_puback" => wrong_puback()
      "timeout_reconnect" => timeout_reconnect()
      "full_inflight_disconnect" => full_inflight_disconnect()
      "partial_suback" => partial_suback()
      "slow_suback_header" | "slow_suback_body" => single_suback()
      "idle_then_suback" => idle_then_suback()
      "cancel_reconnect" => cancel_reconnect()
      "large_incoming_publish" => large_incoming_publish()
      "slow_consumer_overflow" => slow_consumer_overflow()
      _ => fail("unknown fault case")
    }
    println("FAULT_CASE_PASS")
  })
}
