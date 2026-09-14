///|
fn test_session(next_id? : Int = 1) -> Session {
  let (reader, raw_writer) = @io.pipe()
  let writer = @io.BufferedWriter::new(raw_writer, size=16)
  {
    generation: 1,
    reader,
    writer,
    close_transport: () => {
      reader.close()
      raw_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(8)),
    pending: {},
    next_id,
    alive: true,
    last_write: 0L,
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
}

///|
test "abort classifies unsent and started pending requests" {
  let session = test_session()
  let unsent : Pending = {
    id: 1,
    kind: Publish1,
    changed: @cond_var.Cond(),
    started: false,
    result: None,
  }
  let started : Pending = {
    id: 2,
    kind: Publish1,
    changed: @cond_var.Cond(),
    started: true,
    result: None,
  }
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
test "packet id allocation wraps and skips occupied identifiers" {
  let session = test_session(next_id=65535)
  let occupied : Pending = {
    id: 65535,
    kind: Publish1,
    changed: @cond_var.Cond(),
    started: false,
    result: None,
  }
  session.pending[65535] = occupied

  assert_eq(session.allocate(4), 1)
  assert_eq(session.next_id, 2)
  session.abort("test complete")
}

///|
test "packet id allocation enforces inflight limit" {
  let session = test_session(next_id=7)
  let pending : Pending = {
    id: 3,
    kind: Subscribe([]),
    changed: @cond_var.Cond(),
    started: false,
    result: None,
  }
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
