// Proposed regressions for the root package at f0a0e9ec33b7aee2aeee26ea20572904d05e3069.
// NOT COMPILED OR EXECUTED in the audit environment.
// Reuses test_session/test_pending from session_wbtest.mbt.
// Copy to the repository root in an isolated worktree; run its native tests.

///|
fn credit_audit_item(id : Int, kind : RequestKind, bytes : Bytes) -> Outgoing {
  {
    bytes,
    pending: Some(test_pending(id, kind)),
    delivery: None,
    is_ping: false,
  }
}

///|
test "MQTT5 credit audit: SUBSCRIBE bypasses stalled QoS1 PUBLISH" {
  let session = test_session()
  defer session.abort("test complete")
  session.qos1_outstanding = session.receive_maximum
  assert_true(session.enqueue_business(credit_audit_item(2, Publish1, b"publish2")))
  session.business_depth += 1
  assert_true(session.enqueue_business(credit_audit_item(
    3, Subscribe([{ topic: "audit/reply", qos: AtMostOnce }]), b"subscribe",
  )))
  session.business_depth += 1
  let selected = session.next_outgoing()
  // The audited implementation returns None here: PUBLISH#2 is parked.
  assert_true(selected is Some(_))
  assert_eq(selected.unwrap().bytes, b"subscribe")
  assert_eq(session.business_depth, 1)
  assert_eq(session.qos1_outstanding, session.receive_maximum)
  assert_true(session.next_outgoing() is None)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"publish2")
}

///|
test "MQTT5 credit audit: UNSUBSCRIBE bypasses stalled QoS1 PUBLISH" {
  let session = test_session()
  defer session.abort("test complete")
  session.qos1_outstanding = session.receive_maximum
  assert_true(session.enqueue_business(credit_audit_item(2, Publish1, b"publish2")))
  session.business_depth += 1
  assert_true(session.enqueue_business(credit_audit_item(
    3, Unsubscribe(["audit/reply"]), b"unsubscribe",
  )))
  session.business_depth += 1
  let selected = session.next_outgoing()
  assert_true(selected is Some(_))
  assert_eq(selected.unwrap().bytes, b"unsubscribe")
  assert_eq(session.business_depth, 1)
  assert_eq(session.qos1_outstanding, session.receive_maximum)
  assert_true(session.next_outgoing() is None)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"publish2")
}

///|
test "MQTT5 credit audit: bypass preserves PUBLISH FIFO and credit bound" {
  let session = test_session()
  defer session.abort("test complete")
  session.qos1_outstanding = session.receive_maximum
  assert_true(session.enqueue_business(credit_audit_item(2, Publish1, b"publish2")))
  session.business_depth += 1
  assert_true(session.enqueue_business(credit_audit_item(3, Publish1, b"publish3")))
  session.business_depth += 1
  assert_true(session.enqueue_business(credit_audit_item(
    4, Subscribe([{ topic: "audit/reply", qos: AtMostOnce }]), b"subscribe",
  )))
  session.business_depth += 1
  assert_eq(session.next_outgoing().unwrap().bytes, b"subscribe")
  assert_eq(session.business_depth, 2)
  assert_true(session.next_outgoing() is None)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"publish2")
  // next_outgoing only selects; write_one charges quota before its write.
  session.qos1_outstanding += 1
  assert_true(session.next_outgoing() is None)
  session.return_publish_credit()
  assert_eq(session.next_outgoing().unwrap().bytes, b"publish3")
  assert_eq(session.business_depth, 0)
}
