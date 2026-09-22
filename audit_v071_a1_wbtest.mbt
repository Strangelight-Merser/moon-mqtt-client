///|
struct AuditV071A1Writer {
  mut writes : Int
}

///|
impl @io.Writer for AuditV071A1Writer with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  self.writes += 1
  offset + len
}

///|
fn audit_v071_a1_record(
  ever_started : Bool,
  expiry_at_ms : Int64,
) -> DeliveryRecord {
  {
    delivery_id: "audit-expired",
    packet_id: 1,
    changed: @cond_var.Cond(),
    topic: Some("audit/expiry"),
    payload: Some(b"payload"),
    properties: PublishProperties::empty(),
    message_expiry_at_ms: Some(expiry_at_ms),
    retain: false,
    protocol: Mqtt5,
    wire_limit: 4096,
    phase: Queued,
    attempts: 1,
    generation: Some(1),
    ever_started,
    start_deadline: Some(@async.now() + 5000L),
    ack_deadline: None,
    terminal_cause: None,
    acknowledged_reason: None,
    durable: None,
  }
}

///|
fn audit_v071_a1_session(
  delivery : DeliveryRecord,
  writer : &@io.Writer,
  generation? : Int = 1,
) -> Session {
  let (reader, peer_writer) = @io.pipe()
  let attached : Map[Int, DeliveryRecord] = Map([])
  attached[delivery.packet_id] = delivery
  let reserved : Map[Int, DeliveryRecord] = Map([])
  reserved[delivery.packet_id] = delivery
  {
    generation,
    reader,
    writer,
    close_transport: () => {
      reader.close()
      peer_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    deliveries: attached,
    reserved_deliveries: reserved,
    delivery_slots: 1,
    qos1_outstanding: 0,
    receive_maximum: 1,
    deferred_business: [],
    outgoing_limit: 4096,
    delivery_changed: @cond_var.Cond(),
    next_id: 2,
    alive: true,
    last_write: @async.now(),
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
}

///|
fn audit_v071_a1_client(delivery : DeliveryRecord) -> Client {
  let deliveries = DeliveryBook::new()
  deliveries.active[delivery.delivery_id] = delivery
  deliveries.reserved[delivery.packet_id] = delivery
  deliveries.order.push(delivery)
  new_client(
    Config::new("127.0.0.1", "audit-v071-a1", protocol=Mqtt5),
    deliveries,
    None,
  )
}

///|
async test "A1 expired never-written delivery stays not-sent" {
  let writer : AuditV071A1Writer = { writes: 0, }
  let delivery = audit_v071_a1_record(false, durable_now_ms() - 1000L)
  let session = audit_v071_a1_session(delivery, writer)
  let client = audit_v071_a1_client(delivery)
  assert_false(
    session.write_one(
      {
        bytes: b"",
        pending: None,
        delivery: Some(delivery),
        is_ping: false,
      },
      5000,
      5000,
      2,
    ),
  )
  assert_eq(writer.writes, 0)
  client.finish_deliveries(DeliveryAttemptsExhausted("retry exhausted"))
  assert_true(delivery.phase is TerminalNotSent(_))
  assert_eq(client.stats().unknown_outcomes, 0)
}

///|
async test "A1 expired delivery written by an older generation stays unknown" {
  let first_writer : AuditV071A1Writer = { writes: 0, }
  let second_writer : AuditV071A1Writer = { writes: 0, }
  let delivery = audit_v071_a1_record(false, durable_now_ms() + 1000L)
  let client = audit_v071_a1_client(delivery)
  let first = audit_v071_a1_session(delivery, first_writer)
  assert_true(
    first.write_one(
      {
        bytes: b"",
        pending: None,
        delivery: Some(delivery),
        is_ping: false,
      },
      5000,
      5000,
      2,
    ),
  )
  assert_eq(first_writer.writes, 1)
  first.abort("first generation ended after its write")
  @async.sleep(1100)
  delivery.attach(2, @async.now() + 5000L)
  let second = audit_v071_a1_session(delivery, second_writer, generation=2)
  assert_false(
    second.write_one(
      {
        bytes: b"",
        pending: None,
        delivery: Some(delivery),
        is_ping: false,
      },
      5000,
      5000,
      2,
    ),
  )
  assert_eq(second_writer.writes, 0)
  client.finish_deliveries(DeliveryAttemptsExhausted("retry exhausted"))
  assert_true(delivery.phase is TerminalOutcomeUnknown(_))
  assert_eq(client.stats().unknown_outcomes, 1)
}
