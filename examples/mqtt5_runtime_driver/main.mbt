///|
async fn main {
  let args = @env.args()
  guard args.length() == 3 else { abort("expected mode and port") }
  let mode = args[1]
  let resume_enabled = mode == "flow" || mode == "flow-reconnect" ||
    mode == "fresh-resume" ||
    mode == "durable" ||
    mode == "durable-empty" ||
    mode == "durable-negative"
  let config = @mqtt.Config::new(
    "127.0.0.1",
    "moon-mqtt5-runtime-\{mode}",
    port=@string.parse_int(args[2]),
    protocol=@mqtt.Mqtt5,
    session_policy=if resume_enabled {
      @mqtt.ResumeSession
    } else {
      @mqtt.CleanSession
    },
    session_expiry_secs=if resume_enabled { 60L } else { 0L },
    keep_alive_secs=30,
    operation_timeout_ms=if mode == "flow-subscriptions" { 15000 } else { 4000 },
    connect_timeout_ms=4000,
    reconnect_delay_ms=20,
    max_reconnect_delay_ms=50,
    reconnect_attempts=2,
    max_inflight=if mode == "flow-subscriptions" { 8 } else { 4 },
    send_capacity=if mode == "flow-subscriptions" { 8 } else { 64 },
  )
  if mode == "durable" || mode == "durable-empty" || mode == "durable-negative" {
    let path = @env.get_env_var("M1_OUTBOX").unwrap_or_else(() => {
      abort("M1_OUTBOX missing")
    })
    let options = @mqtt.DurableOutboxOptions::new(path, max_records=4)
    @mqtt.with_durable_client(options, config, async fn(client, recovered) {
      if mode == "durable-empty" {
        assert_true(recovered.is_empty())
        println("known-session-empty-ok")
        return
      }
      let handle = if recovered.is_empty() {
        let properties = @mqtt.PublishProperties::new(
          message_expiry_secs=Some(30L),
          response_topic=Some("m1/reply"),
          correlation_data=Some(b"\x00\xff"),
          user_properties=[("origin", "native"), ("origin", "ordered")],
        )
        client.submit_durable_delivery(
          "m1-stable",
          "m1/request",
          b"native5",
          @env.now().reinterpret_as_int64() + 30000L,
          properties~,
        )
      } else {
        assert_eq(recovered.length(), 1)
        recovered[0]
      }
      let status = handle.wait()
      if mode == "durable-negative" {
        guard status.phase is @mqtt.Rejected(reason) else {
          abort("durable negative ACK was not rejection")
        }
        assert_eq(reason.code, 135)
        assert_eq(status.broker_reason, Some(reason))
        println("durable-rejected=135")
      } else {
        assert_true(status.phase is @mqtt.Acknowledged)
        println("durable-acknowledged")
      }
    })
    if mode == "durable-negative" {
      assert_true(@mqtt.inspect_durable_outbox(options, config).is_empty())
    }
    return
  }
  @async.with_timeout(if mode == "flow-subscriptions" { 20000 } else { 9000 }, async fn() {
    @mqtt.with_client(config, async fn(client) {
      match mode {
        "flow-subscriptions" => credit_nonpublish(client)
        "flow" | "flow-reconnect" => {
          let first = client.submit_delivery("first", "m1/request", b"first")
          let second = client.submit_delivery("second", "m1/request", b"second")
          let status = first.wait()
          assert_true(status.phase is @mqtt.Acknowledged)
          guard status.broker_reason is Some(reason) else {
            abort("missing MQTT5 PUBACK reason")
          }
          println("reason=\{reason.code}")
          assert_true(second.wait().phase is @mqtt.Acknowledged)
        }
        "negative" =>
          try
            client.publish_detailed(
              "m1/request",
              b"denied",
              qos=@mqtt.AtLeastOnce,
            )
          catch {
            @mqtt.BrokerRejected(reason) => {
              let detail = reason.reason_string.unwrap_or("")
              println("rejected=\{reason.code} \{detail}")
            }
            error => raise error
          } noraise {
            _ => abort("negative PUBACK reported success")
          }
        "fresh-resume" => abort("unowned retained session was accepted")
        "keepalive" => @async.sleep(2300)
        "packet-limit" | "qos-limit" | "retain-limit" => {
          let payload = if mode == "packet-limit" {
            b"this publication intentionally exceeds the negotiated thirty-two byte packet limit"
          } else {
            b"payload"
          }
          try
            client.publish_detailed(
              "m1/request",
              payload,
              qos=@mqtt.AtLeastOnce,
              retain=mode == "retain-limit",
            )
          catch {
            @mqtt.BrokerRejected(_) =>
              abort("local limit fabricated a broker acknowledgement")
            _ => println("local-rejection")
          } noraise {
            _ => abort("negotiated limit ignored")
          }
        }
        "disconnect-reason" =>
          while true {
            ignore(client.next_event())
          }
        "subscription-reasons" => {
          let results = client.subscribe_detailed([
            { topic: "m1/allowed", qos: @mqtt.AtLeastOnce, },
            { topic: "m1/denied", qos: @mqtt.AtLeastOnce, },
          ])
          assert_eq(results[0].reason.code, 1)
          assert_eq(results[1].reason.code, 135)
          let reasons = client.unsubscribe_detailed(["m1/allowed", "m1/denied"])
          assert_eq(reasons[0].code, 0)
          assert_eq(reasons[1].code, 135)
          try client.unsubscribe(["m1/denied"]) catch {
            @mqtt.BrokerRejected(reason) => assert_eq(reason.code, 135)
            error => raise error
          } noraise {
            _ => abort("unsubscribe hid rejection")
          }
          assert_true(
            client.publish_detailed("m1/after", b"alive", qos=@mqtt.AtLeastOnce)
            is @mqtt.Accepted(_),
          )
          println("subscription-reasons-ok")
        }
        "metadata" => {
          ignore(
            client.subscribe([{ topic: "m1/reply", qos: @mqtt.AtLeastOnce, }]),
          )
          let properties = @mqtt.PublishProperties::new(
            message_expiry_secs=Some(30L),
            response_topic=Some("m1/reply"),
            correlation_data=Some(b"\x00\xff"),
            user_properties=[("origin", "native"), ("origin", "ordered")],
          )
          assert_true(
            client.publish_detailed(
              "m1/request",
              b"native5",
              qos=@mqtt.AtLeastOnce,
              properties~,
            )
            is @mqtt.Accepted(_),
          )
          while true {
            match client.next_event() {
              @mqtt.MessageReceived(message) => {
                assert_eq(message.topic, "m1/reply")
                assert_eq(message.payload, b"paho5")
                assert_eq(
                  message.properties.correlation_data,
                  Some(b"\x00\xff"),
                )
                assert_eq(message.properties.user_properties, [
                  ("origin", "paho"),
                ])
                guard message.properties.message_expiry_secs is Some(expiry) else {
                  abort("missing incoming expiry")
                }
                assert_true(expiry > 0L && expiry <= 20L)
                println("metadata-ok")
                break
              }
              _ => ()
            }
          }
        }
        _ => abort("unknown runtime test mode")
      }
    }) catch {
      @mqtt.ServerDisconnected(reason) if mode == "disconnect-reason" =>
        println("disconnect=\{reason.code}")
      error => raise error
    }
  })
}

///|
/// The raw peer holds both subscription acknowledgements until all admissions
/// are visible. Polling observes state; no elapsed sleep establishes ordering.
async fn wait_pending(client : @mqtt.Client, count : Int) -> Unit {
  @async.with_timeout(3000, async fn() {
    while client.stats().pending_requests != count {
      @async.sleep(1)
    }
  })
}

///|
async fn credit_nonpublish(client : @mqtt.Client) -> Unit {
  @async.with_task_group(group => {
    let first = group.spawn(() => {
      client.publish("h01/request", b"first", qos=@mqtt.AtLeastOnce)
    })
    wait_pending(client, 1)
    @async.with_timeout(3000, async fn() {
      while client.stats().business_queue_depth != 0 {
        @async.sleep(1)
      }
    })
    let second = group.spawn(() => {
      client.publish("h01/request", b"second", qos=@mqtt.AtLeastOnce)
    })
    wait_pending(client, 2)
    assert_eq(client.stats().business_queue_depth, 1)
    @stdio.stdout.write(@utf8.encode("h01: second-admitted pending=2 business=1\n"))
    let sub = group.spawn(() => {
      ignore(client.subscribe([{ topic: "h01/reply", qos: @mqtt.AtMostOnce }]))
    })
    wait_pending(client, 3)
    let unsub = group.spawn(() => client.unsubscribe(["h01/reply"]))
    wait_pending(client, 4)
    @stdio.stdout.write(@utf8.encode("h01: subscriptions-admitted pending=4\n"))
    sub.wait()
    unsub.wait()
    assert_eq(client.stats().pending_requests, 2)
    assert_eq(client.stats().business_queue_depth, 1)
    // A further non-PUBLISH round trip proves the scheduler made progress after
    // processing both acknowledgements, with the second PUBLISH still blocked.
    ignore(client.subscribe([{ topic: "h01/barrier", qos: @mqtt.AtMostOnce }]))
    first.wait()
    second.wait()
    @stdio.stdout.write(@utf8.encode("h01: complete\n"))
  })
}
