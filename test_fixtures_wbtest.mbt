///|
/// Shared private state only; each test supplies its own transport and oracle.
fn fixture_session(
  reader : &@io.Reader,
  writer : &@io.Writer,
  close_transport : () -> Unit,
  generation? : Int = 1,
  send_capacity? : Int = 8,
  control_capacity? : Int = 4,
  pending? : Map[Int, Pending] = {},
  deliveries? : Map[Int, DeliveryRecord] = {},
  reserved_deliveries? : Map[Int, DeliveryRecord] = {},
  delivery_slots? : Int = 0,
  receive_maximum? : Int = 65535,
  outgoing_limit? : Int = 65536,
  next_id? : Int = 1,
  last_write? : Int64 = 0L,
) -> Session {
  {
    generation, reader, writer, close_transport,
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(send_capacity)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(control_capacity)),
    business_depth: 0, control_depth: 0, pending, deliveries, reserved_deliveries,
    delivery_slots, qos1_outstanding: 0, receive_maximum, deferred_business: [],
    outgoing_limit, delivery_changed: @cond_var.Cond(), next_id, alive: true,
    last_write, heartbeat_state: Idle, early_messages: [], failure_reason: None,
  }
}

///|
/// Build a pending request for queue tests.
fn test_pending(id : Int, kind : RequestKind, queued_at? : Int64 = 0L, started? : Bool = false) -> Pending {
  {
    id,
    kind,
    changed: @cond_var.Cond(),
    started,
    result: None,
    queued_at,
    refresh: None,
  }
}

