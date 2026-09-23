///|
fn test_session(next_id? : Int = 1, outgoing_limit? : Int = 65536) -> Session {
  let (reader, raw_writer) = @io.pipe()
  let writer = @io.BufferedWriter::new(raw_writer, size=16)
  fixture_session(
    reader,
    writer,
    () => {
      reader.close()
      raw_writer.close()
    },
    send_capacity=8,
    control_capacity=4,
    outgoing_limit=outgoing_limit,
    next_id=next_id,
  )
}

///|
test "reserved control slots stay usable when the business queue is full" {
  let session = test_session()
  // Saturate the business queue with ordinary publishes.
  for index in 0..<8 {
    let request = test_pending(index + 1, Publish1)
    session.pending[index + 1] = request
    assert_true(
      session.outgoing.try_put({
        bytes: b"publish",
        pending: Some(request),
        delivery: None,
        is_ping: false,
      }),
    )
    session.business_depth += 1
  }
  // A further business publish is refused...
  assert_false(
    session.outgoing.try_put({
      bytes: b"overflow",
      pending: None,
      delivery: None,
      is_ping: false,
    }),
  )
  // ...but protocol control packets still have their own reserved slots.
  session.control(@codec.PubackPacket({ message_id: 7 }), 1024)
  session.control(@codec.PingreqPacket, 1024, is_ping=true)
  assert_eq(session.control_depth, 2)
  assert_eq(session.business_depth, 8)
  session.abort("test complete")
}

///|
test "abort zeroes the queue occupancy mirrors" {
  let session = test_session()
  ignore(
    session.outgoing.try_put({
      bytes: b"business",
      pending: None,
      delivery: None,
      is_ping: false,
    }),
  )
  session.business_depth += 1
  session.control(@codec.PingreqPacket, 1024, is_ping=true)
  assert_eq(session.business_depth, 1)
  assert_eq(session.control_depth, 1)

  session.abort("transport lost")

  // The queued items were discarded, so stats must not keep reporting them.
  assert_eq(session.business_depth, 0)
  assert_eq(session.control_depth, 0)
}

///|
test "control exhaustion aborts instead of dropping a control packet" {
  let session = test_session()
  for _ in 0..<4 {
    session.control(@codec.PingreqPacket, 1024, is_ping=true)
  }
  try {
    session.control(@codec.PingrespPacket, 1024)
    abort("a full control queue must terminate the session")
  } catch {
    ProtocolError(reason) =>
      assert_true(reason.contains("control queue exhausted"))
    _ => abort("expected ProtocolError when control slots are exhausted")
  }
  session.abort("test complete")
}

///|
test "control packets are dequeued before business traffic in FIFO order" {
  let session = test_session()
  let first = test_pending(1, Publish1)
  let second = test_pending(2, Publish1)
  ignore(
    session.outgoing.try_put({
      bytes: b"one",
      pending: Some(first),
      delivery: None,
      is_ping: false,
    }),
  )
  session.business_depth += 1
  ignore(
    session.outgoing.try_put({
      bytes: b"two",
      pending: Some(second),
      delivery: None,
      is_ping: false,
    }),
  )
  session.business_depth += 1
  session.control(@codec.PingreqPacket, 1024, is_ping=true)

  // Control first, then business FIFO; each pop decrements its own depth.
  let control_item = session.next_outgoing()
  assert_true(control_item is Some(item) && item.is_ping)
  assert_eq(session.control_depth, 0)
  assert_eq(session.business_depth, 2)
  let one = session.next_outgoing()
  assert_eq(one.unwrap().bytes, b"one")
  assert_eq(session.business_depth, 1)
  let two = session.next_outgoing()
  assert_eq(two.unwrap().bytes, b"two")
  assert_eq(session.business_depth, 0)
  assert_true(session.next_outgoing() is None)
  session.abort("test complete")
}

///|
test "abort classifies unsent and started pending requests" {
  let session = test_session()
  let unsent : Pending = test_pending(1, Publish1)
  let started : Pending = test_pending(2, Publish1, started=true)
  session.pending[unsent.id] = unsent
  session.pending[started.id] = started

  session.abort("transport lost")

  match unsent.result {
    Some(Err(NotSent(reason))) => assert_eq(reason, "transport lost")
    _ => abort("an unstarted request must complete as NotSent")
  }
  match started.result {
    Some(Err(OutcomeUnknown(reason))) => assert_eq(reason, "transport lost")
    _ => abort("a started request must complete as OutcomeUnknown")
  }
  assert_false(session.alive)
  assert_eq(session.pending.length(), 0)
}

///|
test "DISCONNECT local write completion wins a later reader close exactly once" {
  let completed_session = test_session()
  let completed = test_pending(0, Disconnect, started=true)
  completed.local_write_complete = true
  completed_session.pending[0] = completed
  completed_session.abort("reader closed after local write")
  match completed.result {
    Some(Ok(Done)) => ()
    _ => abort("completed local DISCONNECT write must succeed")
  }

  let partial_session = test_session()
  let partial = test_pending(0, Disconnect, started=true)
  partial_session.pending[0] = partial
  partial_session.abort("reader closed during write")
  match partial.result {
    Some(Err(OutcomeUnknown(_))) => ()
    _ => abort("started, incomplete DISCONNECT must remain unknown")
  }

  let queued_session = test_session()
  let queued = test_pending(0, Disconnect)
  queued_session.pending[0] = queued
  queued_session.abort("reader closed before write")
  match queued.result {
    Some(Err(NotSent(_))) => ()
    _ => abort("unstarted DISCONNECT must remain not sent")
  }

  let settled_session = test_session()
  let settled = test_pending(0, Disconnect, started=true)
  settled.finish(Err(OutcomeUnknown("first terminal result")))
  settled.local_write_complete = true
  settled_session.pending[0] = settled
  settled_session.abort("late reader close")
  match settled.result {
    Some(Err(OutcomeUnknown(reason))) => assert_eq(reason, "first terminal result")
    _ => abort("late success must not overwrite a published terminal result")
  }
}

///|
test "packet id allocation wraps and skips occupied identifiers" {
  let session = test_session(next_id=65535)
  let occupied : Pending = test_pending(65535, Publish1)
  session.pending[65535] = occupied

  assert_eq(session.allocate(4), 1)
  assert_eq(session.next_id, 2)
  session.abort("test complete")
}

///|
test "packet id allocation enforces inflight limit" {
  let session = test_session(next_id=7)
  let pending : Pending = test_pending(3, Subscribe([]))
  session.pending[3] = pending

  try {
    ignore(session.allocate(1))
    abort("allocation must fail when the inflight limit is reached")
  } catch {
    Backpressure(reason) => assert_eq(reason, "inflight limit reached")
    _ => abort("expected Backpressure at the inflight limit")
  }
  assert_eq(session.next_id, 7)
  session.abort("test complete")
}

///|
test "client identity is rejected on a plain connection" {
  let config = Config::new(
    "localhost",
    "mtls-plain",
    client_identity=Some({
      certificate_chain_file: "client.pem",
      private_key_file: "client.key",
    }),
  )
  try {
    config.validate()
    abort("plain TLS mode must reject a client identity")
  } catch {
    InvalidConfig(reason) =>
      assert_true(reason.contains("client identity requires TLS"))
    _ => abort("expected InvalidConfig")
  }
}

///|
test "configuration rejects invalid Will topic names" {
  for topic in ["", "devices/+", "devices/#"] {
    let config = Config::new(
      "localhost",
      "will-topic-test",
      will=Some({ topic, payload: b"offline", qos: AtLeastOnce, retain: true, }),
    )
    try {
      config.validate()
      abort("invalid Will topic must be rejected")
    } catch {
      InvalidConfig(_) => ()
      _ => abort("expected InvalidConfig for an invalid Will topic")
    }
  }
}
