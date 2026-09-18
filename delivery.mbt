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
  packet_id : Int
  attempts : Int
  generation : Int?
} derive(Eq, @debug.Debug)

///|
priv struct DeliveryRecord {
  delivery_id : String
  packet_id : Int
  changed : @cond_var.Cond
  mut topic : String?
  mut payload : Bytes?
  retain : Bool
  mut phase : DeliveryPhase
  mut attempts : Int
  mut generation : Int?
  mut ever_started : Bool
  mut ack_deadline : Int64?
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
  {
    active: {},
    reserved: {},
    order: [],
    next_packet_id: 1,
  }
}

///|
fn DeliveryPhase::terminal(self : DeliveryPhase) -> Bool {
  self is (Acknowledged | TerminalNotSent(_) | TerminalOutcomeUnknown(_))
}

///|
fn DeliveryRecord::status(self : DeliveryRecord) -> DeliveryStatus {
  {
    delivery_id: self.delivery_id,
    phase: self.phase,
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
fn DeliveryRecord::packet(self : DeliveryRecord, limit : Int) -> Bytes raise ClientError {
  guard self.topic is Some(topic) && self.payload is Some(payload) else {
    raise Closed
  }
  encode(
    @codec.PublishPacket({
      topic,
      payload,
      qos: @codec.QoS1,
      message_id: Some(self.packet_id),
      retain: self.retain,
      dup: self.ever_started,
    }),
    limit,
  )
}

///|
fn DeliveryRecord::attach(self : DeliveryRecord, generation : Int) -> Unit {
  self.attempts += 1
  self.generation = Some(generation)
  self.ack_deadline = None
  self.phase = Queued
  self.changed.broadcast()
}

///|
fn DeliveryRecord::rollback_initial_attach(self : DeliveryRecord) -> Unit {
  self.attempts = 0
  self.generation = None
  self.ack_deadline = None
  self.phase = Queued
}

///|
fn DeliveryRecord::mark_started(self : DeliveryRecord) -> Unit {
  self.ever_started = true
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
    self.ack_deadline = None
    self.phase = AwaitingReconnect(reason)
    self.changed.broadcast()
  }
}

///|
fn DeliveryRecord::finish_acknowledged(self : DeliveryRecord) -> Unit {
  if !self.phase.terminal() {
    self.ack_deadline = None
    self.phase = Acknowledged
    self.topic = None
    self.payload = None
    self.changed.broadcast()
  }
}

///|
fn DeliveryRecord::finish_terminal(self : DeliveryRecord, reason : String) -> Unit {
  if !self.phase.terminal() {
    self.ack_deadline = None
    self.generation = None
    self.phase = if self.ever_started {
      TerminalOutcomeUnknown(reason)
    } else {
      TerminalNotSent(reason)
    }
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
fn DeliveryBook::finish_all(self : DeliveryBook, reason : String) -> Unit {
  let records = self.order.copy()
  for record in records {
    record.finish_terminal(reason)
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
