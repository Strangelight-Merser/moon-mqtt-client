///|
fn credit_enqueue(session : Session, item : Outgoing) -> Unit raise {
  assert_true(session.enqueue_business(item))
  session.business_depth += 1
}

///|
test "deferred publications share capacity and preserve mixed FIFO" {
  let session = test_session()
  defer session.abort("test complete")
  session.qos1_outstanding = session.receive_maximum
  credit_enqueue(session, credit_audit_item(1, Publish1, b"one"))
  credit_enqueue(session, credit_audit_item(0, Publish0, b"zero"))
  credit_enqueue(session, credit_audit_item(2, Publish1, b"two"))
  credit_enqueue(session, credit_audit_item(3, Subscribe([]), b"sub"))
  credit_enqueue(session, credit_audit_item(4, Unsubscribe([]), b"unsub"))
  assert_eq(session.next_outgoing().unwrap().bytes, b"sub")
  assert_eq(session.deferred_business.length(), 3)
  assert_eq(session.business_depth, 4)
  assert_eq(session.next_outgoing().unwrap().bytes, b"unsub")
  assert_true(session.next_outgoing() is None)
  for i in 0..<5 {
    credit_enqueue(session, credit_audit_item(i + 5, Publish1, b"tail"))
  }
  assert_eq(session.business_depth, 8)
  assert_true(session.next_outgoing() is None)
  assert_eq(session.deferred_business.length(), 8)
  assert_false(session.enqueue_business(credit_audit_item(10, Subscribe([]), b"full")))
  session.control(@codec.PubackPacket({ message_id: 44 }), 1024)
  assert_eq(session.next_outgoing().unwrap().bytes, b"\x40\x02\x00\x2c")
  assert_eq(session.business_depth, 8)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"one")
  session.qos1_outstanding += 1
  assert_eq(session.next_outgoing().unwrap().bytes, b"zero")
  assert_true(session.next_outgoing() is None)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"two")
}

///|
test "available credit keeps original business order" {
  let session = test_session()
  defer session.abort("test complete")
  for (id, kind, bytes) in [
    (1, Publish1, b"first"),
    (2, Subscribe([]), b"second"),
    (0, Publish0, b"third"),
    (3, Unsubscribe([]), b"fourth"),
  ] {
    credit_enqueue(session, credit_audit_item(id, kind, bytes))
  }
  for bytes in [b"first", b"second", b"third", b"fourth"] {
    assert_eq(session.next_outgoing().unwrap().bytes, bytes)
  }
  assert_eq(session.business_depth, 0)
  assert_true(session.deferred_business.is_empty())
}

///|
async test "cancelled deferred request is never written and abort clears buffer" {
  let session = test_session()
  defer session.abort("test complete")
  let request : Pending = {
    id: 1, kind: Publish1, changed: @cond_var.Cond(), started: false,
    result: None, queued_at: @async.now(), refresh: None,
  }
  let item : Outgoing = {
    bytes: b"must-not-be-written", pending: Some(request), delivery: None, is_ping: false,
  }
  session.qos1_outstanding = session.receive_maximum
  credit_enqueue(session, item)
  assert_true(session.next_outgoing() is None)
  request.finish(Err(NotSent("cancelled")))
  session.return_publish_credit()
  assert_true(session.write_one(session.next_outgoing().unwrap(), 4000, 100, 0))
  assert_false(request.started)
  // Refill and defer more than one item before generation teardown.
  session.qos1_outstanding = session.receive_maximum
  credit_enqueue(session, credit_audit_item(2, Publish1, b"two"))
  credit_enqueue(session, credit_audit_item(3, Publish1, b"three"))
  assert_true(session.next_outgoing() is None)
  session.abort("generation ended")
  assert_eq(session.business_depth, 0)
  assert_true(session.deferred_business.is_empty())
  assert_true(session.next_outgoing() is None)
}
