///|
test "MQTT 3.1.1 stays the default and rejects session expiry" {
  let defaults = Config::new("localhost", "default")
  assert_true(defaults.protocol is Mqtt311)
  defaults.validate()
  try {
    Config::new("localhost", "invalid", session_expiry_secs=60L).validate()
    abort("MQTT 3.1.1 accepted Session Expiry")
  } catch {
    InvalidConfig(_) => ()
    _ => abort("wrong invalid configuration error")
  }
}

///|
test "publish expiry is recomputed from one immutable deadline" {
  let properties = PublishProperties::new(message_expiry_secs=Some(10L), user_properties=[
    ("trace", "one"),
  ])
  let first = publish_properties_wire(properties, Some(20000L), 10000L)
  let later = publish_properties_wire(properties, Some(20000L), 16000L)
  assert_true(first.contains(@mqtt5.MessageExpiry(10L)))
  assert_true(later.contains(@mqtt5.MessageExpiry(4L)))
  assert_true(later.contains(@mqtt5.UserProperty("trace", "one")))
  try {
    ignore(publish_properties_wire(properties, Some(20000L), 20000L))
    abort("elapsed message expiry was extended")
  } catch {
    DeliveryRejected(_) => ()
    _ => abort("wrong elapsed-expiry error")
  }
}

///|
test "MQTT 5 acknowledgement keeps numeric code and diagnostic" {
  match
    normalize_packet(
      Incoming5(@mqtt5.Puback(7, 0x97, [@mqtt5.ReasonString("quota")])),
    ) {
    RuntimePuback(id, reason) => {
      assert_eq(id, 7)
      assert_eq(reason.code, 0x97)
      assert_eq(reason.reason_string, Some("quota"))
    }
    _ => abort("PUBACK was not normalized")
  }
}

///|
test "negotiated settings apply server reductions" {
  let config = Config::new(
    "localhost",
    "limits",
    protocol=Mqtt5,
    session_policy=ResumeSession,
    session_expiry_secs=300L,
    max_inflight=32,
    max_packet_size=65536,
    keep_alive_secs=30,
  )
  let value = negotiated_settings(config, [
    @mqtt5.ReceiveMaximum(3),
    @mqtt5.MaximumPacketSize(4096L),
    @mqtt5.MaximumQoS(0),
    @mqtt5.RetainAvailable(0),
    @mqtt5.ServerKeepAlive(12),
    @mqtt5.SessionExpiry(120L),
  ])
  assert_eq(value.receive_maximum, 3)
  assert_eq(value.max_packet_size, 4096)
  assert_true(value.maximum_qos is AtMostOnce)
  assert_false(value.retain_available)
  assert_eq(value.keep_alive_secs, 12)
  assert_eq(value.session_expiry_secs, 120L)
}

///|
async test "known session marker survives an empty durable outbox" {
  let directory = @fs.tmpdir(prefix="mqtt5-known-session")
  defer @fs.rmdir(directory, recursive=true)
  let options = DurableOutboxOptions::new("\{directory}/outbox.sqlite3")
  let first = SqliteOutbox::open(options, "mqtt5-known")
  assert_false(first.has_known_session())
  first.mark_session_known()
  assert_true(first.has_known_session())
  assert_true(first.inspect().is_empty())
  first.close()
  let reopened = SqliteOutbox::open(options, "mqtt5-known")
  assert_true(reopened.has_known_session())
  assert_true(reopened.inspect().is_empty())
  reopened.close()
}

///|
test "durable identity binds protocol and session options" {
  let mqtt311 = Config::new(
    "localhost",
    "identity",
    session_policy=ResumeSession,
  )
  let mqtt5 = Config::new(
    "localhost",
    "identity",
    protocol=Mqtt5,
    session_policy=ResumeSession,
    session_expiry_secs=60L,
  )
  let mqtt5_longer = Config::new(
    "localhost",
    "identity",
    protocol=Mqtt5,
    session_policy=ResumeSession,
    session_expiry_secs=120L,
  )
  assert_not_eq(durable_identity(mqtt311), durable_identity(mqtt5))
  assert_not_eq(durable_identity(mqtt5), durable_identity(mqtt5_longer))
}

///|
test "credit-stalled FIFO head stays within the business queue bound" {
  let session = test_session()
  defer session.abort("test complete")
  session.qos1_outstanding = session.receive_maximum
  // Non-PUBLISH requests do not consume receive credit.
  let subscribe : Outgoing = {
    bytes: b"subscribe",
    pending: Some(test_pending(1, Subscribe([]))),
    delivery: None,
    is_ping: false,
  }
  assert_true(session.enqueue_business(subscribe))
  session.business_depth += 1
  assert_true(session.next_outgoing() is Some(_))
  for i in 0..<8 {
    assert_true(
      session.enqueue_business({
        bytes: b"publish",
        pending: Some(test_pending(i + 2, Publish1)),
        delivery: None,
        is_ping: false,
      }),
    )
    session.business_depth += 1
  }
  assert_true(session.next_outgoing() is None)
  assert_eq(session.business_depth, 8)
  assert_false(session.enqueue_business(subscribe))
  session.control(@codec.PubackPacket({ message_id: 7, }), 1024)
  assert_eq(session.next_outgoing().unwrap().bytes, b"\x40\x02\x00\x07")
  session.return_publish_credit()
  assert_true(session.next_outgoing() is Some(_))
  assert_eq(session.business_depth, 7)
}

///|
test "negotiated maximum packet size also bounds control packets" {
  let session = test_session(outgoing_limit=3)
  defer session.abort("test complete")
  let config = Config::new("localhost", "control-bound", protocol=Mqtt5)
  try {
    session.control_wire(@codec.PubackPacket({ message_id: 7, }), config)
    abort("oversized control packet was queued")
  } catch {
    ProtocolError(_) => ()
    _ => abort("wrong control packet bound error")
  }
  assert_eq(session.control_depth, 0)
}
