// Regression origin: PR #4, audit_v071_a1_wbtest.mbt. Original assertions retained.
///|
struct ExpirySpyWriter {
  mut writes : Int
}

///|
impl @io.Writer for ExpirySpyWriter with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  self.writes += 1
  offset + len
}

///|
fn expiry_test_record(
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
fn expiry_test_session(
  delivery : DeliveryRecord,
  writer : &@io.Writer,
  generation? : Int = 1,
) -> Session {
  let (reader, peer_writer) = @io.pipe()
  let attached : Map[Int, DeliveryRecord] = Map([])
  attached[delivery.packet_id] = delivery
  let reserved : Map[Int, DeliveryRecord] = Map([])
  reserved[delivery.packet_id] = delivery
  fixture_session(
    reader,
    writer,
    () => {
      reader.close()
      peer_writer.close()
    },
    send_capacity=2,
    control_capacity=2,
    generation=generation,
    deliveries=attached,
    reserved_deliveries=reserved,
    delivery_slots=1,
    receive_maximum=1,
    outgoing_limit=4096,
    next_id=2,
    last_write=@async.now(),
  )
}

///|
fn expiry_test_client(delivery : DeliveryRecord) -> Client {
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
  let writer : ExpirySpyWriter = { writes: 0, }
  let delivery = expiry_test_record(false, durable_now_ms() - 1000L)
  let session = expiry_test_session(delivery, writer)
  let client = expiry_test_client(delivery)
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
  let first_writer : ExpirySpyWriter = { writes: 0, }
  let second_writer : ExpirySpyWriter = { writes: 0, }
  let delivery = expiry_test_record(false, durable_now_ms() + 1000L)
  let client = expiry_test_client(delivery)
  let first = expiry_test_session(delivery, first_writer)
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
  let second = expiry_test_session(delivery, second_writer, generation=2)
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
