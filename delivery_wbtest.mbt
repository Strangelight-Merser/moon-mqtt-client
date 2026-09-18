///|
fn delivery_test_record(
  id : String,
  packet_id : Int,
) -> DeliveryRecord {
  {
    delivery_id: id,
    packet_id,
    changed: @cond_var.Cond(),
    topic: Some("q1/test"),
    payload: Some(b"payload"),
    retain: false,
    phase: Queued,
    attempts: 0,
    generation: None,
    ever_started: false,
    start_deadline: None,
    ack_deadline: None,
    terminal_cause: None,
  }
}

///|
struct DeliverySpyWriter {
  mut writes : Int
}

///|
impl @io.Writer for DeliverySpyWriter with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  self.writes += 1
  offset + len
}

///|
fn delivery_test_session(
  reserved? : Map[Int, DeliveryRecord] = {},
  generation? : Int = 1,
) -> Session {
  let (reader, writer) = @io.pipe()
  {
    generation,
    reader,
    writer,
    close_transport: () => {
      reader.close()
      writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(1)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(1)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    deliveries: {},
    reserved_deliveries: reserved,
    delivery_slots: 0,
    delivery_changed: @cond_var.Cond(),
    next_id: 1,
    alive: true,
    last_write: @async.now(),
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
}

///|
fn delivery_test_client(config : Config) -> Client {
  let deliveries = DeliveryBook::new()
  let client : Client = {
    config,
    events: @aqueue.Queue(kind=@aqueue.Blocking(config.receive_capacity)),
    changed: @cond_var.Cond(),
    subscriptions: {},
    deliveries,
    session: None,
    ready: true,
    stopped: false,
    generation: 1,
    worker: None,
    event_depth: 0,
    reconnects: 0,
    disconnects: 0,
    unknown_outcomes: 0,
    last_disconnect: None,
    backoff: ReconnectBackoff::new(1),
    stop_reason: None,
  }
  let session = delivery_test_session(reserved=deliveries.reserved)
  client.session = Some(session)
  client
}

///|
test "retained packet identifiers are skipped without consuming restore capacity" {
  let retained = delivery_test_record("retained", 1)
  let reserved : Map[Int, DeliveryRecord] = { 1: retained, }
  let session = delivery_test_session(reserved~)
  assert_eq(session.allocate(1), 2)
  session.abort("test complete")
}

///|
async test "cancelled handle wait does not cancel its delivery" {
  let record = delivery_test_record("stable", 7)
  let handle : DeliveryHandle = { record, }
  @async.with_task_group(group => {
    let waiter = group.spawn(() => handle.wait())
    waiter.cancel()
    try {
      ignore(waiter.wait())
    } catch {
      _ => ()
    }
  })
  assert_true(handle.status().phase is Queued)
  record.finish_acknowledged()
  let status = handle.wait()
  assert_true(status.phase is Acknowledged)
  assert_eq(status.delivery_id, "stable")
  assert_eq(status.packet_id, 7)
}

///|
test "terminal state distinguishes never-started and possibly-sent delivery" {
  let unsent = delivery_test_record("unsent", 1)
  unsent.finish_terminal(
    "broker session lost",
    BrokerSessionLost("broker session lost"),
  )
  assert_true(
    unsent.status().phase is TerminalNotSent("broker session lost"),
  )
  assert_true(
    unsent.status().terminal_cause is Some(BrokerSessionLost(_)),
  )
  let started = delivery_test_record("started", 2)
  started.attach(1, @async.now() + 1000L)
  started.mark_started()
  started.finish_terminal(
    "delivery attempts exhausted",
    DeliveryAttemptsExhausted("delivery attempts exhausted"),
  )
  assert_true(
    started.status().phase is TerminalOutcomeUnknown(
      "delivery attempts exhausted",
    ),
  )
  assert_true(
    started.status().terminal_cause is Some(DeliveryAttemptsExhausted(_)),
  )
}

///|
async test "resume admission is bounded and ordinary QoS1 is explicit" {
  let config = Config::new(
    "127.0.0.1",
    "q1-admission",
    session_policy=ResumeSession,
    max_inflight=1,
    send_capacity=1,
  )
  let client = delivery_test_client(config)
  let handle = client.submit_delivery("delivery-1", "q1/test", b"one")
  let status = handle.status()
  assert_true(status.phase is Queued)
  assert_eq(status.attempts, 1)
  try {
    ignore(client.submit_delivery("delivery-2", "q1/test", b"two"))
    abort("max_inflight=1 accepted a second delivery")
  } catch {
    Backpressure(_) => ()
    _ => abort("expected Backpressure")
  }
  try {
    client.publish("q1/test", b"ordinary", qos=AtLeastOnce)
    abort("ordinary resume QoS1 publish must be rejected")
  } catch {
    DeliveryRejected(_) => ()
    _ => abort("expected DeliveryRejected")
  }
  let session = client.session.unwrap()
  session.abort("test complete")
  client.finish_deliveries(SessionLost("test complete"))
}

///|
test "resume rejects unrepresentable attachment budget" {
  let config = Config::new(
    "127.0.0.1",
    "q1-budget-overflow",
    session_policy=ResumeSession,
    reconnect_attempts=0x7FFFFFFF,
  )
  try {
    config.validate()
    abort("unrepresentable ResumeSession budget was accepted")
  } catch {
    InvalidConfig(reason) => assert_true(reason.contains("too large"))
    _ => abort("expected InvalidConfig")
  }
}

///|
test "oversized delivery payload is rejected before ownership" {
  let config = Config::new(
    "127.0.0.1",
    "q1-payload-bound",
    session_policy=ResumeSession,
    max_packet_size=8,
  )
  let client = delivery_test_client(config)
  try {
    ignore(client.submit_delivery("too-large", "q1/test", b"12345"))
    abort("oversized delivery payload was accepted")
  } catch {
    ProtocolError(reason) => assert_true(reason.contains("payload exceeds"))
    _ => abort("expected ProtocolError")
  }
  let session = client.session.unwrap()
  assert_true(client.deliveries.active.is_empty())
  assert_true(client.deliveries.reserved.is_empty())
  assert_eq(session.delivery_slots, 0)
  assert_eq(session.business_depth, 0)
  session.abort("test complete")
}

///|
async test "ACK watchdog exhausts the configured generation budget" {
  let config = Config::new(
    "127.0.0.1",
    "q1-watchdog",
    session_policy=ResumeSession,
    operation_timeout_ms=1,
    reconnect_attempts=0,
  )
  let session = delivery_test_session()
  let delivery = delivery_test_record("expires", 4)
  delivery.attach(session.generation, @async.now() + 1000L)
  delivery.mark_started()
  delivery.mark_written(@async.now() - 1L)
  session.deliveries[delivery.packet_id] = delivery
  session.delivery_slots = 1
  try {
    session.delivery_watchdog(config)
    abort("watchdog did not fail")
  } catch {
    DeliveryAttemptsExhausted(reason) => assert_true(reason.contains("expires"))
    _ => abort("expected DeliveryAttemptsExhausted")
  }
  assert_true(session.failure_reason is Some(DeliveryAttemptsExhausted(_)))
  assert_true(delivery.phase is AwaitingReconnect(_))
}

///|
async test "queued delivery deadline survives control priority and stays not sent" {
  let config = Config::new(
    "127.0.0.1",
    "q1-queued-deadline",
    session_policy=ResumeSession,
    operation_timeout_ms=1,
    reconnect_attempts=1,
  )
  let session = delivery_test_session()
  let delivery = delivery_test_record("never-started", 5)
  delivery.attach(session.generation, @async.now() - 1L)
  session.deliveries[delivery.packet_id] = delivery
  session.delivery_slots = 1
  assert_true(
    session.outgoing.try_put({
      bytes: delivery.packet(1024),
      pending: None,
      delivery: Some(delivery),
      is_ping: false,
    }),
  )
  session.business_depth = 1
  session.control(@codec.PingreqPacket, 1024, is_ping=true)
  let preferred = session.next_outgoing().unwrap()
  assert_true(preferred.is_ping)
  assert_eq(session.business_depth, 1)
  session.control(@codec.PingreqPacket, 1024, is_ping=true)
  try {
    @async.with_timeout(100, () => session.delivery_watchdog(config))
    abort("queued delivery watchdog did not fail")
  } catch {
    DeliveryAttemptTimeout(reason) =>
      assert_true(reason.contains("did not begin its socket write"))
    @async.TimeoutError => abort("queued delivery had no start deadline")
    _ => abort("expected DeliveryAttemptTimeout")
  }
  assert_false(delivery.ever_started)
  assert_true(delivery.phase is AwaitingReconnect(_))
  delivery.finish_terminal("attempt stopped", ReconnectExhausted("attempt stopped"))
  assert_true(delivery.phase is TerminalNotSent("attempt stopped"))
  assert_eq(session.business_depth, 0)
}

///|
async test "dequeued expired delivery never reaches socket writer" {
  let writer : DeliverySpyWriter = { writes: 0, }
  let session = cause_test_session(writer)
  let delivery = delivery_test_record("expired-before-start", 6)
  delivery.attach(session.generation, @async.now() - 1L)
  session.deliveries[delivery.packet_id] = delivery
  session.delivery_slots = 1
  assert_false(
    session.write_one(
      {
        bytes: delivery.packet(1024),
        pending: None,
        delivery: Some(delivery),
        is_ping: false,
      },
      1,
      50,
      1,
    ),
  )
  assert_eq(writer.writes, 0)
  assert_false(delivery.ever_started)
  assert_true(session.failure_reason is Some(DeliveryAttemptTimeout(_)))
  assert_true(delivery.phase is AwaitingReconnect(_))
}

///|
async test "replay preserves admission order through a one-slot queue" {
  let config = Config::new(
    "127.0.0.1",
    "q1-replay-order",
    session_policy=ResumeSession,
    max_inflight=2,
    send_capacity=1,
  )
  let client = delivery_test_client(config)
  let first = delivery_test_record("first", 9)
  let second = delivery_test_record("second", 10)
  first.attach(1, @async.now() + 1000L)
  first.mark_started()
  first.detach(1, "connection lost")
  second.attach(1, @async.now() + 1000L)
  second.detach(1, "connection lost")
  for record in [first, second] {
    client.deliveries.active[record.delivery_id] = record
    client.deliveries.reserved[record.packet_id] = record
    client.deliveries.order.push(record)
  }
  let session = delivery_test_session(
    reserved=client.deliveries.reserved,
    generation=2,
  )
  @async.with_task_group(group => {
    let replay = group.spawn(() => client.replay_deliveries(session))
    while second.generation != Some(2) {
      @async.sleep(1)
    }
    assert_eq(session.business_depth, 1)
    let first_item = session.next_outgoing().unwrap()
    assert_eq(first_item.delivery.unwrap().delivery_id, "first")
    assert_true((first_item.bytes[0].to_int() & 0x08) == 0x08)
    replay.wait()
    assert_eq(session.business_depth, 1)
    let second_item = session.next_outgoing().unwrap()
    assert_eq(second_item.delivery.unwrap().delivery_id, "second")
    assert_true((second_item.bytes[0].to_int() & 0x08) == 0)
  })
  assert_eq(first.attempts, 2)
  assert_eq(second.attempts, 2)
  assert_eq(session.business_depth, 0)
  session.abort("test complete")
  client.finish_deliveries(SessionLost("test complete"))
}

///|
async test "unexpected PUBACK settles recoverable handle and releases ownership" {
  let wrong_ack = encode(@codec.PubackPacket({ message_id: 99 }), 1024)
  let reader = @io.MemoryReader(async fn(writer) { writer.write(wrong_ack) })
  let (_, pipe_writer) = @io.pipe()
  let config = Config::new(
    "127.0.0.1",
    "q1-malformed-ack",
    session_policy=ResumeSession,
  )
  let client = cause_test_client(config)
  let delivery = delivery_test_record("malformed-ack", 7)
  client.deliveries.active[delivery.delivery_id] = delivery
  client.deliveries.reserved[delivery.packet_id] = delivery
  client.deliveries.order.push(delivery)
  delivery.attach(1, @async.now() + 1000L)
  delivery.mark_started()
  let attached : Map[Int, DeliveryRecord] = Map([])
  attached[delivery.packet_id] = delivery
  let session : Session = {
    generation: 1,
    reader,
    writer: pipe_writer,
    close_transport: () => {
      reader.close()
      pipe_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(1)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(1)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    deliveries: attached,
    reserved_deliveries: client.deliveries.reserved,
    delivery_slots: 1,
    delivery_changed: @cond_var.Cond(),
    next_id: 1,
    alive: true,
    last_write: 0L,
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
  client.read_loop(session)
  match session.failure_reason {
    Some(ProtocolError(reason)) =>
      assert_eq(reason, "unexpected PUBACK identifier")
    _ => abort("unexpected PUBACK did not remain a protocol failure")
  }
  client.finish_deliveries(session.failure_reason.unwrap())
  assert_true(
    delivery.phase is TerminalOutcomeUnknown("unexpected PUBACK identifier"),
  )
  assert_true(client.deliveries.active.is_empty())
  assert_true(client.deliveries.reserved.is_empty())
  assert_true(client.deliveries.order.is_empty())
  assert_eq(session.delivery_slots, 0)
}
