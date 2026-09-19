///|
/// MQTT session behavior. `CleanSession` preserves the original client
/// semantics. `ResumeSession` enables explicit recoverable QoS 1 admissions.
pub(all) enum SessionPolicy {
  CleanSession
  ResumeSession
} derive(Eq, @debug.Debug)

///|
/// Application-visible state of one admitted recoverable QoS 1 delivery.
pub(all) enum DeliveryPhase {
  Queued
  AwaitingAck
  AwaitingReconnect(String)
  Acknowledged
  Rejected(BrokerReason)
  TerminalNotSent(String)
  TerminalOutcomeUnknown(String)
} derive(Eq, @debug.Debug)

///|
/// Immutable diagnostic snapshot. `attempts` counts physical connection
/// generations to which this delivery has been attached, including a
/// generation lost before its socket write began.
pub(all) struct DeliveryStatus {
  delivery_id : String
  phase : DeliveryPhase
  /// Typed initiating cause for terminal failure; absent while active and
  /// after successful acknowledgement.
  terminal_cause : ClientError?
  broker_reason : BrokerReason?
  packet_id : Int
  attempts : Int
  generation : Int?
} derive(@debug.Debug)

///|
/// Persisted state returned by standalone durable-outbox inspection.
pub(all) enum DurableDeliveryState {
  DurablePending
  DurableBlocked(String)
} derive(Eq, @debug.Debug)

///|
/// One durable record in stable insertion order. Payload bytes are returned so
/// an operator can reconcile the exact admitted command without opening the
/// SQLite file through a second implementation.
pub(all) struct DurableDeliveryInfo {
  delivery_id : String
  packet_id : Int
  sequence : Int64
  topic : String
  payload : Bytes
  properties : PublishProperties
  message_expiry_at_ms : Int64?
  retain : Bool
  expires_at_ms : Int64
  attempts : Int
  ever_started : Bool
  state : DurableDeliveryState
} derive(@debug.Debug)

///|
/// Concrete runtime ownership shared by the client and its durable records.
/// The FIFO semaphore serializes storage transitions with their in-memory
/// publication; SqliteOutbox separately serializes its own statements.
priv struct DurableRuntime {
  outbox : SqliteOutbox
  gate : @semaphore.Semaphore
  recovered_requires_session_present : Bool
  mut known_session : Bool
  /// Wall clock seam keeps absolute-expiry transition tests deterministic.
  now_ms : () -> Int64
}

///|
priv struct DurableRecordMeta {
  runtime : DurableRuntime
  expires_at_ms : Int64
}

///|
/// Serialize one durable transition and keep its SQLite commit plus matching
/// memory publication indivisible with respect to coroutine cancellation.
async fn[T] DurableRuntime::transition(
  self : DurableRuntime,
  action : async () -> T,
) -> T {
  self.gate.acquire()
  defer self.gate.release()
  @async.protect_from_cancel(action)
}

///|
priv struct DeliveryRecord {
  delivery_id : String
  packet_id : Int
  changed : @cond_var.Cond
  mut topic : String?
  mut payload : Bytes?
  properties : PublishProperties
  message_expiry_at_ms : Int64?
  retain : Bool
  protocol : ProtocolVersion
  mut wire_limit : Int
  mut phase : DeliveryPhase
  mut attempts : Int
  mut generation : Int?
  mut ever_started : Bool
  mut start_deadline : Int64?
  mut ack_deadline : Int64?
  mut terminal_cause : ClientError?
  mut durable : DurableRecordMeta?
}

///|
/// A handle observes a delivery but does not own its protocol lifetime.
pub struct DeliveryHandle {
  priv record : DeliveryRecord
}

///|
priv struct DeliveryBook {
  active : Map[String, DeliveryRecord]
  reserved : Map[Int, DeliveryRecord]
  order : Array[DeliveryRecord]
  mut next_packet_id : Int
}

///|
fn DeliveryBook::new() -> DeliveryBook {
  { active: {}, reserved: {}, order: [], next_packet_id: 1, }
}

///|
fn DeliveryPhase::terminal(self : DeliveryPhase) -> Bool {
  self
  is (Acknowledged
  | Rejected(_)
  | TerminalNotSent(_)
  | TerminalOutcomeUnknown(_))
}

///|
fn DeliveryRecord::status(self : DeliveryRecord) -> DeliveryStatus {
  {
    delivery_id: self.delivery_id,
    phase: self.phase,
    terminal_cause: self.terminal_cause,
    broker_reason: match self.phase {
      Rejected(reason) => Some(reason)
      _ => None
    },
    packet_id: self.packet_id,
    attempts: self.attempts,
    generation: self.generation,
  }
}

///|
pub fn DeliveryHandle::id(self : DeliveryHandle) -> String {
  self.record.delivery_id
}

///|
pub fn DeliveryHandle::status(self : DeliveryHandle) -> DeliveryStatus {
  self.record.status()
}

///|
/// Wait until the delivery reaches one terminal state. Cancelling this wait has
/// no effect on the delivery, its connection, or another waiter.
pub async fn DeliveryHandle::wait(self : DeliveryHandle) -> DeliveryStatus {
  while !self.record.phase.terminal() {
    self.record.changed.wait()
  }
  self.record.status()
}

///|
fn DeliveryRecord::packet(
  self : DeliveryRecord,
  limit? : Int? = None,
) -> Bytes raise ClientError {
  guard self.topic is Some(topic) && self.payload is Some(payload) else {
    raise Closed
  }
  let now_ms = match self.durable {
    Some(meta) => (meta.runtime.now_ms)()
    None => durable_now_ms()
  }
  encode_wire(
    self.protocol,
    @codec.PublishPacket({
      topic,
      payload,
      qos: @codec.QoS1,
      message_id: Some(self.packet_id),
      retain: self.retain,
      dup: self.ever_started,
    }),
    publish_properties_wire(self.properties, self.message_expiry_at_ms, now_ms),
    limit.unwrap_or(self.wire_limit),
  )
}

///|
fn DeliveryRecord::attach(
  self : DeliveryRecord,
  generation : Int,
  start_deadline : Int64,
) -> Unit {
  self.attempts += 1
  self.generation = Some(generation)
  self.start_deadline = Some(start_deadline)
  self.ack_deadline = None
  self.terminal_cause = None
  self.phase = Queued
  self.changed.broadcast()
}

///|
fn DeliveryRecord::rollback_initial_attach(self : DeliveryRecord) -> Unit {
  self.attempts = 0
  self.generation = None
  self.start_deadline = None
  self.ack_deadline = None
  self.terminal_cause = None
  self.phase = Queued
}

///|
fn DeliveryRecord::mark_started(self : DeliveryRecord) -> Unit {
  self.ever_started = true
  self.start_deadline = None
  self.phase = AwaitingAck
  self.changed.broadcast()
}

///|
fn DeliveryRecord::mark_written(
  self : DeliveryRecord,
  deadline : Int64,
) -> Unit {
  if !self.phase.terminal() {
    self.ack_deadline = Some(deadline)
    self.changed.broadcast()
  }
}

///|
fn DeliveryRecord::detach(
  self : DeliveryRecord,
  generation : Int,
  reason : String,
) -> Unit {
  if !self.phase.terminal() && self.generation == Some(generation) {
    self.generation = None
    self.start_deadline = None
    self.ack_deadline = None
    self.phase = AwaitingReconnect(reason)
    self.changed.broadcast()
  }
}

///|
fn DeliveryRecord::finish_acknowledged(self : DeliveryRecord) -> Unit {
  if !self.phase.terminal() {
    self.start_deadline = None
    self.ack_deadline = None
    self.phase = Acknowledged
    self.topic = None
    self.payload = None
    self.changed.broadcast()
  }
}

///|
/// Publish a broker rejection only after any durable DELETE has committed.
fn DeliveryRecord::finish_rejected(
  self : DeliveryRecord,
  reason : BrokerReason,
) -> Unit {
  if !self.phase.terminal() {
    self.start_deadline = None
    self.ack_deadline = None
    self.generation = None
    self.phase = Rejected(reason)
    self.terminal_cause = Some(BrokerRejected(reason))
    self.topic = None
    self.payload = None
    self.changed.broadcast()
  }
}

///|
fn DeliveryRecord::finish_terminal(
  self : DeliveryRecord,
  reason : String,
  cause : ClientError,
) -> Unit {
  if !self.phase.terminal() {
    self.start_deadline = None
    self.ack_deadline = None
    self.generation = None
    self.phase = if self.ever_started {
      TerminalOutcomeUnknown(reason)
    } else {
      TerminalNotSent(reason)
    }
    self.terminal_cause = Some(cause)
    self.topic = None
    self.payload = None
    self.changed.broadcast()
  }
}

///|
fn DeliveryBook::remove_record(
  self : DeliveryBook,
  record : DeliveryRecord,
) -> Unit {
  self.active.remove(record.delivery_id)
  self.reserved.remove(record.packet_id)
  for index, candidate in self.order {
    if candidate.delivery_id == record.delivery_id {
      ignore(self.order.remove(index))
      break
    }
  }
}

///|
fn DeliveryBook::acknowledge(
  self : DeliveryBook,
  record : DeliveryRecord,
) -> Unit {
  record.finish_acknowledged()
  self.remove_record(record)
}

///|
fn DeliveryBook::reject(
  self : DeliveryBook,
  record : DeliveryRecord,
  reason : BrokerReason,
) -> Unit {
  record.finish_rejected(reason)
  self.remove_record(record)
}

///|
fn DeliveryBook::finish_all(
  self : DeliveryBook,
  reason : String,
  cause : ClientError,
) -> Unit {
  let records = self.order.copy()
  for record in records {
    record.finish_terminal(reason, cause)
  }
  self.active.clear()
  self.reserved.clear()
  self.order.clear()
}

///|
fn DeliveryBook::allocate_packet_id(
  self : DeliveryBook,
  session : Session,
) -> Int raise ClientError {
  for _ in 0..<65535 {
    let id = self.next_packet_id
    self.next_packet_id = if id == 65535 { 1 } else { id + 1 }
    if !self.reserved.contains(id) && !session.pending.contains(id) {
      return id
    }
  }
  raise Backpressure("all packet identifiers are occupied")
}

///|
fn valid_delivery_id(id : String) -> Bool {
  let size = @utf8.encode(id).length()
  size > 0 && size <= 128
}
