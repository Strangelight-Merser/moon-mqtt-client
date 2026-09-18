///|
struct Client {
  config : Config
  events : @aqueue.Queue[Event]
  changed : @cond_var.Cond
  subscriptions : Map[String, QoS]
  deliveries : DeliveryBook
  mut session : Session?
  mut ready : Bool
  mut stopped : Bool
  mut generation : Int
  mut worker : @async.Task[Unit]?
  /// Diagnostics counters. They are plain increments on the owning tasks; the
  /// snapshot is best-effort and does not claim cross-field atomicity.
  mut event_depth : Int
  mut reconnects : Int
  mut disconnects : Int
  mut unknown_outcomes : Int
  mut last_disconnect : String?
  mut backoff : ReconnectBackoff
  /// Set when the supervisor ends because a connect-phase or terminal error
  /// should reach waiters. Normal shutdown leaves this empty so they see Closed.
  mut stop_reason : ClientError?
}

///|
/// Bounded exponential backoff with jitter. The jitter comes from a per-client
/// xorshift stream whose default seed normally comes from operating-system
/// entropy, avoiding one shared retry schedule for every client.
priv struct ReconnectBackoff {
  mut seed : Int
}

///|
let reconnect_seed_counter : @ref.Ref[UInt64] = @ref.Ref(0UL)

///|
fn ReconnectBackoff::new_default_with(
  entropy : Bytes?,
  now : UInt64,
  sequence : UInt64,
) -> ReconnectBackoff {
  let seed = match entropy {
    Some(bytes) => {
      if bytes.length() != 4 {
        abort("reconnect entropy must be four bytes")
      }
      bytes[0].to_int() |
      (bytes[1].to_int() << 8) |
      (bytes[2].to_int() << 16) |
      (bytes[3].to_int() << 24)
    }
    None => (now ^ (sequence * 0x9E3779B97F4A7C15UL)).to_int()
  }
  ReconnectBackoff::new(seed)
}

///|
fn ReconnectBackoff::new(seed : Int) -> ReconnectBackoff {
  // Zero is a fixed point of xorshift, so replace it with a constant.
  { seed: if seed == 0 { 0x9E3779B9 } else { seed } }
}

///|
fn ReconnectBackoff::new_default() -> ReconnectBackoff {
  reconnect_seed_counter.val += 1UL
  ReconnectBackoff::new_default_with(
    @env.rand(4),
    @env.now(),
    reconnect_seed_counter.val,
  )
}

///|
fn ReconnectBackoff::next(self : ReconnectBackoff) -> Int {
  let mut x = self.seed
  x = x ^ (x << 13)
  x = x ^ (x >> 17)
  x = x ^ (x << 5)
  self.seed = x
  // Keep jitter in 0..base/2 without depending on the sign bit.
  ((x & 0x7FFFFFFF) % 1000)
}

///|
/// Own all client tasks within the callback's lifetime.
pub async fn[T] with_client(config : Config, action : async (Client) -> T) -> T {
  config.validate()
  ignore(encode(config.connect_packet(), config.max_packet_size))
  let client : Client = {
    config,
    events: @aqueue.Queue(kind=@aqueue.Blocking(config.receive_capacity)),
    changed: @cond_var.Cond(),
    subscriptions: {},
    deliveries: DeliveryBook::new(),
    session: None,
    ready: false,
    stopped: false,
    generation: 0,
    worker: None,
    event_depth: 0,
    reconnects: 0,
    disconnects: 0,
    unknown_outcomes: 0,
    last_disconnect: None,
    backoff: ReconnectBackoff::new_default(),
    stop_reason: None,
  }
  @async.with_task_group(group => {
    let worker = group.spawn(() => client.supervise())
    client.worker = Some(worker)
    defer {
      client.stopped = true
      client.ready = false
      if client.session is Some(session) {
        session.abort("client scope ended")
      }
      client.finish_deliveries(SessionLost("client scope ended"))
      worker.cancel()
      client.events.close(error=Closed)
      client.changed.broadcast()
    }
    client.wait_connected()
    let result = action(client)
    client.disconnect()
    result
  })
}

///|
/// Pin the per-client reconnect jitter stream. Intended for tests that need a
/// reproducible backoff sequence; the value never reaches the wire. Setting it
/// after a reconnect has started only affects subsequent attempts.
pub fn Client::set_reconnect_seed(self : Client, seed : Int) -> Unit {
  self.backoff = ReconnectBackoff::new(seed)
}

///|
/// Read-only runtime snapshot. Safe to call from any task; it never blocks.
pub fn Client::stats(self : Client) -> ClientStats {
  let (business, control, inflight) = match self.session {
    Some(session) if session.alive => (
      session.business_depth,
      session.control_depth,
      session.pending.length() + session.delivery_slots,
    )
    _ => (0, 0, 0)
  }
  {
    generation: self.generation,
    connected: self.ready,
    business_queue_depth: business,
    control_queue_depth: control,
    event_queue_depth: self.event_depth,
    pending_requests: inflight,
    reconnects: self.reconnects,
    disconnects: self.disconnects,
    unknown_outcomes: self.unknown_outcomes,
    last_disconnect: self.last_disconnect,
  }
}

///|
fn client_error_of(error : Error) -> ClientError {
  try {
    raise error
  } catch {
    InvalidConfig(msg) => InvalidConfig(msg)
    ProtocolError(msg) => ProtocolError(msg)
    NotConnected => NotConnected
    Closed => Closed
    Backpressure(msg) => Backpressure(msg)
    NotSent(msg) => NotSent(msg)
    OutcomeUnknown(msg) => OutcomeUnknown(msg)
    ConnectionRefused(msg) => ConnectionRefused(msg)
    ReconnectExhausted(msg) => ReconnectExhausted(msg)
    DeliveryRejected(msg) => DeliveryRejected(msg)
    BrokerSessionLost(msg) => BrokerSessionLost(msg)
    DeliveryAttemptsExhausted(msg) => DeliveryAttemptsExhausted(msg)
    OperationTimeout(msg) => OperationTimeout(msg)
    WriteTimeout(msg) => WriteTimeout(msg)
    PingResponseTimeout(msg) => PingResponseTimeout(msg)
    TlsFailure(msg) => TlsFailure(msg)
    // A TLS 1.3 server can reject the client identity after the local handshake
    // call has already returned: the alert then surfaces on the first MQTT
    // CONNECT write or the CONNACK read. Keep the TLS category instead of
    // degrading it to the generic ProtocolError fallback.
    @tls.TlsError(msg) => TlsFailure(msg)
    _ => ProtocolError(error.to_string())
  }
}

///|
fn Client::record_stop(self : Client, error : Error) -> Unit {
  if self.stop_reason is None {
    self.stop_reason = Some(client_error_of(error))
  }
}

///|
fn Client::close_reason(self : Client) -> ClientError {
  match self.stop_reason {
    Some(err) => err
    None => Closed
  }
}

///|
pub async fn Client::wait_connected(self : Client) -> Unit {
  while !self.ready || !(self.session is Some(session) && session.alive) {
    if self.stopped {
      raise self.close_reason()
    }
    self.changed.wait()
  }
}

///|
pub async fn Client::next_event(self : Client) -> Event {
  let event = self.events.get()
  if self.event_depth > 0 {
    self.event_depth -= 1
  }
  event
}

///|
fn Client::emit(self : Client, event : Event) -> Unit raise {
  if !self.events.try_put(event) {
    raise Backpressure(
      "event queue full; client terminates instead of dropping messages",
    )
  }
  self.event_depth += 1
  match event {
    Disconnected(_, reason) => {
      self.disconnects += 1
      self.last_disconnect = Some(reason)
    }
    _ => ()
  }
}

///|
fn Client::active(self : Client) -> Session raise ClientError {
  if self.stopped {
    raise self.close_reason()
  }
  match self.session {
    Some(session) if self.ready && session.alive => session
    _ => raise NotConnected
  }
}

///|
async fn Session::request(
  self : Session,
  config : Config,
  kind : RequestKind,
  packet : (Int) -> @codec.Packet,
  abort_session : (String) -> Unit,
  on_unknown? : () -> Unit = () => (),
) -> Reply {
  if !self.alive {
    raise NotConnected
  }
  // DISCONNECT has no wire identifier. Slot zero reserves one bounded close
  // completion even when every ordinary inflight slot is occupied.
  let id = if kind is Disconnect {
    0
  } else {
    self.allocate(config.max_inflight)
  }
  let bytes = encode(packet(id), config.max_packet_size)
  let request : Pending = {
    id,
    kind,
    changed: @cond_var.Cond(),
    started: false,
    result: None,
    queued_at: @async.now(),
  }
  self.pending[id] = request
  // Cancelling an in-flight request is conservative by design: the connection is
  // aborted, because after the bytes may have reached the peer the client cannot
  // know whether the broker executed the request. Every pending request is
  // therefore settled exactly once, as `NotSent` or `OutcomeUnknown`, and a late
  // ACK from the old generation can never complete a new request. `client` is the
  // owner of the wake-up, so the closure is passed in by the caller.
  defer (if request.result is None { abort_session("request cancelled") })
  if !(kind is Disconnect) {
    let accepted = self.outgoing.try_put({
      bytes,
      pending: Some(request),
      delivery: None,
      is_ping: false,
    }) catch {
      _ => false
    }
    if !accepted {
      self.pending.remove(id)
      request.finish(Err(Backpressure("send queue full")))
      raise Backpressure("send queue full")
    }
    self.business_depth += 1
  }
  if kind is Disconnect {
    // DISCONNECT still completes when the business queue is full: the control
    // slots are reserved and never occupied by business traffic.
    try {
      self.control_queue.put({
        bytes,
        pending: Some(request),
        delivery: None,
        is_ping: false,
      })
      self.control_depth += 1
    } catch {
      _ => raise NotSent("control queue closed before DISCONNECT was queued")
    }
  }
  // Budget the whole operation, including the time it spent queued.
  //
  // Deliberately not `@async.with_timeout`: that helper implements its timer as
  // a failing task-group child. This watchdog returns normally and only wakes
  // the condition variable; the request coroutine owns deadline classification
  // and session abort, so its cancellation state remains clean.
  let deadline = request.queued_at + config.operation_timeout_ms.to_int64()
  @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, () => {
      let remaining = deadline - @async.now()
      if remaining > 0 {
        @async.sleep(remaining.to_int())
      }
      request.changed.broadcast()
    })
    while request.result is None && @async.now() < deadline {
      request.changed.wait()
    }
  })
  if request.result is None && @async.now() >= deadline {
    // The caller-side budget expired. Aborting settles this request and every
    // other pending request exactly once.
    abort_session("operation_timeout_ms=\{config.operation_timeout_ms} expired")
  }
  match request.result {
    Some(Ok(reply)) => reply
    Some(Err(error)) => {
      match error {
        OutcomeUnknown(_) => on_unknown()
        _ => ()
      }
      raise error
    }
    None => raise Closed
  }
}

///|
/// Count a request that may have executed without an acknowledgement. Called on
/// every `OutcomeUnknown` completion so `Client::stats` can expose it.
fn Client::note_unknown(self : Client) -> Unit {
  self.unknown_outcomes += 1
}

///|
/// Abort the current session and wake the supervisor.
///
/// A task-group child that raises cannot wake a group body parked on
/// `Cond::wait`, so any code that kills a session must broadcast `changed`
/// explicitly. Without this, a caller-side abort (operation timeout or
/// cancellation) leaves the supervisor asleep and the next connection is never
/// attempted.
fn Client::abort_session(self : Client, reason : String) -> Unit {
  match self.session {
    Some(session) => session.abort(reason)
    None => ()
  }
  self.changed.broadcast()
}

///|
pub async fn Client::publish(
  self : Client,
  topic : String,
  payload : Bytes,
  qos? : QoS = AtMostOnce,
  retain? : Bool = false,
) -> Unit {
  if self.config.session_policy is ResumeSession && qos == AtLeastOnce {
    raise DeliveryRejected(
      "QoS 1 publish under ResumeSession requires submit_delivery",
    )
  }
  let session = self.active()
  if payload.length() > self.config.max_packet_size - 4 {
    raise ProtocolError("outgoing payload exceeds packet limit")
  }
  ignore(
    session.request(
      self.config,
      if qos == AtMostOnce {
        Publish0
      } else {
        Publish1
      },
      id => {
        @codec.PublishPacket({
          topic,
          payload,
          qos: qos.wire(),
          message_id: if qos == AtMostOnce {
            None
          } else {
            Some(id)
          },
          retain,
          dup: false,
        })
      },
      reason => self.abort_session(reason),
      on_unknown=() => self.note_unknown(),
    ),
  )
}

///|
/// Atomically admit one reconnect-resilient QoS 1 delivery. Admission is
/// connected-only and never becomes an offline queue.
pub fn Client::submit_delivery(
  self : Client,
  delivery_id : String,
  topic : String,
  payload : Bytes,
  retain? : Bool = false,
) -> DeliveryHandle raise ClientError {
  if !(self.config.session_policy is ResumeSession) {
    raise DeliveryRejected("submit_delivery requires ResumeSession")
  }
  if !valid_delivery_id(delivery_id) {
    raise DeliveryRejected("delivery_id must contain 1..128 UTF-8 bytes")
  }
  if self.deliveries.active.contains(delivery_id) {
    raise DeliveryRejected("duplicate active delivery_id: \{delivery_id}")
  }
  let session = self.active()
  if self.deliveries.active.length() + session.pending.length() >=
    self.config.max_inflight {
    raise Backpressure("inflight limit reached")
  }
  let packet_id = self.deliveries.allocate_packet_id(session)
  let record : DeliveryRecord = {
    delivery_id,
    packet_id,
    changed: @cond_var.Cond(),
    topic: Some(topic),
    payload: Some(payload),
    retain,
    phase: Queued,
    attempts: 0,
    generation: None,
    ever_started: false,
    ack_deadline: None,
  }
  // Encode before mutating ownership so an invalid topic/packet bound leaves no
  // active ID, packet reservation, or queue entry behind.
  let bytes = record.packet(self.config.max_packet_size)
  self.deliveries.active[delivery_id] = record
  self.deliveries.reserved[packet_id] = record
  self.deliveries.order.push(record)
  record.attach(session.generation)
  session.deliveries[packet_id] = record
  session.delivery_slots += 1
  let accepted = session.outgoing.try_put({
    bytes,
    pending: None,
    delivery: Some(record),
    is_ping: false,
  }) catch {
    _ => false
  }
  if !accepted {
    session.deliveries.remove(packet_id)
    session.delivery_slots -= 1
    record.rollback_initial_attach()
    self.deliveries.remove_record(record)
    raise Backpressure("send queue full")
  }
  session.business_depth += 1
  { record, }
}

///|
fn Client::finish_deliveries(self : Client, error : Error) -> Unit {
  if self.deliveries.active.is_empty() {
    return
  }
  for record in self.deliveries.order {
    if !record.phase.terminal() && record.ever_started {
      self.unknown_outcomes += 1
    }
  }
  self.deliveries.finish_all(session_error_reason(error))
}

///|
pub async fn Client::subscribe(
  self : Client,
  topics : Array[Subscription],
) -> Array[SubscriptionResult] {
  self.subscribe_on(self.active(), topics.copy())
}

///|
async fn Client::subscribe_on(
  self : Client,
  session : Session,
  topics : Array[Subscription],
) -> Array[SubscriptionResult] {
  match
    session.request(self.config, Subscribe(topics), id => {
      @codec.SubscribePacket({
        message_id: id,
        topics: topics.map(t => { topic: t.topic, qos: t.qos.wire(), }),
      })
    }, reason => self.abort_session(reason), on_unknown=() => self.note_unknown()) {
    Subscriptions(results) => results
    _ => raise ProtocolError("wrong subscribe completion")
  }
}

///|
pub async fn Client::unsubscribe(self : Client, topics : Array[String]) -> Unit {
  let session = self.active()
  let topics = topics.copy()
  ignore(
    session.request(self.config, Unsubscribe(topics), id => {
      @codec.UnsubscribePacket({ message_id: id, topics, })
    }, reason => self.abort_session(reason), on_unknown=() => self.note_unknown()),
  )
}

///|
/// Stop reconnect, send DISCONNECT when possible, and await worker cleanup.
pub async fn Client::disconnect(self : Client) -> Unit {
  if self.stopped {
    return
  }
  self.stopped = true
  self.ready = false
  defer {
    if self.session is Some(session) {
      session.abort("client disconnected")
    }
    self.finish_deliveries(SessionLost("client disconnected"))
    if self.worker is Some(worker) {
      worker.cancel()
    }
    self.events.close(error=Closed)
    self.changed.broadcast()
  }
  if self.session is Some(session) && session.alive {
    ignore(
      session.request(
        self.config,
        Disconnect,
        _ => @codec.DisconnectPacket,
        reason => self.abort_session(reason),
      ),
    )
    // EMQX closes after processing MQTT DISCONNECT. Give the peer a bounded
    // chance to close before tearing down WebSocket framing: an immediate
    // Close can discard already-received MQTT frames in that broker.
    // MQTT requires the client to close, so a silent peer is bounded by
    // write_timeout_ms and unconditional cleanup closes the transport.
    // The reader answers any received WebSocket Close during this interval.
    if self.config.transport is WebSocket(_) && session.alive {
      @async.with_timeout_opt(self.config.write_timeout_ms, async fn() {
        while session.alive {
          self.changed.wait()
        }
      }) |> ignore
    }
  }
  if self.session is Some(session) {
    session.abort("client disconnected")
  }
  if self.worker is Some(worker) {
    worker.cancel()
    // Explicit shutdown already settles requests and closes the transport;
    // the worker may finish with cancellation or a concurrent transport error.
    worker.wait() catch {
      _ => ()
    }
  }
}
