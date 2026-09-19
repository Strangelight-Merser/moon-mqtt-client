///|
struct Client {
  config : Config
  events : @aqueue.Queue[Event]
  changed : @cond_var.Cond
  subscriptions : Map[String, QoS]
  deliveries : DeliveryBook
  durable : DurableRuntime?
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
  mut negotiated : NegotiatedSettings?
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
fn durable_now_ms() -> Int64 {
  @env.now().reinterpret_as_int64()
}

///|
/// A length-prefixed versioned identity avoids delimiter collisions while
/// binding one file to the logical MQTT endpoint and stable client identity.
fn durable_identity(config : Config) -> String {
  let (scheme, target) = match (config.transport, config.tls) {
    (Tcp, Plain) => ("mqtt", "")
    (Tcp, _) => ("mqtts", "")
    (WebSocket(path), Plain) =>
      ("ws", "\{@utf8.encode(path).length()}:\{path}")
    (WebSocket(path), _) =>
      ("wss", "\{@utf8.encode(path).length()}:\{path}")
  }
  let username = match config.username {
    Some(value) => "some:\{@utf8.encode(value).length()}:\{value}"
    None => "none"
  }
  "moon-mqtt-durable-v1|\{scheme}|\{@utf8.encode(config.host).length()}:\{config.host}|\{config.port}|\{target}|\{@utf8.encode(config.client_id).length()}:\{config.client_id}|\{username}"
}

///|
fn PersistedDelivery::public_info(self : PersistedDelivery) -> DurableDeliveryInfo {
  {
    delivery_id: self.delivery_id,
    packet_id: self.packet_id,
    sequence: self.sequence,
    topic: self.topic,
    payload: self.payload,
    retain: self.retain,
    expires_at_ms: self.expires_at_ms,
    attempts: self.attempts,
    ever_started: self.ever_started,
    state: match self.state {
      Pending => DurablePending
      Blocked => DurableBlocked(self.reason)
    },
  }
}

///|
fn new_client(
  config : Config,
  deliveries : DeliveryBook,
  durable : DurableRuntime?,
) -> Client {
  {
    config,
    events: @aqueue.Queue(kind=@aqueue.Blocking(config.receive_capacity)),
    changed: @cond_var.Cond(),
    subscriptions: {},
    deliveries,
    durable,
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
    negotiated: None,
  }
}

///|
/// Own all client tasks within the callback's lifetime.
pub async fn[T] with_client(config : Config, action : async (Client) -> T) -> T {
  config.validate()
  ignore(config.connect_bytes())
  let client = new_client(config, DeliveryBook::new(), None)
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
/// Inspect a durable outbox without starting a network connection. The file is
/// opened with the same exclusive ownership and identity checks as a client
/// scope, then closed after all asynchronous SQLite work has settled.
pub async fn inspect_durable_outbox(
  options : DurableOutboxOptions,
  config : Config,
) -> Array[DurableDeliveryInfo] {
  config.validate()
  if !(config.session_policy is ResumeSession) {
    raise InvalidConfig("durable outbox requires ResumeSession")
  }
  let outbox = SqliteOutbox::open(options, durable_identity(config))
  errdefer (try outbox.close() catch { _ => () })
  let records = outbox.inspect()
  outbox.close()
  records.map(record => record.public_info())
}

///|
/// Discard one record only when storage proves no write could have started.
/// This standalone operation refuses concurrent ownership of the same file.
pub async fn discard_durable_delivery(
  options : DurableOutboxOptions,
  config : Config,
  delivery_id : String,
) -> Unit {
  config.validate()
  if !(config.session_policy is ResumeSession) {
    raise InvalidConfig("durable outbox requires ResumeSession")
  }
  let outbox = SqliteOutbox::open(options, durable_identity(config))
  errdefer (try outbox.close() catch { _ => () })
  outbox.discard_never_started(delivery_id)
  outbox.close()
}

///|
/// Own a ResumeSession client and one concrete SQLite outbox for the callback's
/// lifetime. Recovered handles are materialized, ordered and packet-reserved
/// before the supervisor can dial. The callback receives those exact objects
/// after the first connection becomes ready.
pub async fn[T] with_durable_client(
  options : DurableOutboxOptions,
  config : Config,
  action : async (Client, Array[DeliveryHandle]) -> T,
) -> T {
  config.validate()
  if !(config.session_policy is ResumeSession) {
    raise InvalidConfig("durable outbox requires ResumeSession")
  }
  if options.max_records > config.max_inflight {
    raise InvalidConfig(
      "durable max_records must not exceed client max_inflight",
    )
  }
  ignore(config.connect_bytes())
  let outbox = SqliteOutbox::open(options, durable_identity(config))
  errdefer (try outbox.close() catch { _ => () })
  ignore(outbox.expire(durable_now_ms()))
  let persisted = outbox.inspect()
  if persisted.length() > config.max_inflight {
    raise DurableStoreLimit(
      "persisted durable records exceed client max_inflight",
    )
  }
  for record in persisted {
    if record.state is Blocked {
      raise DurableStoreState(
        "durable delivery \{record.delivery_id} is blocked: \{record.reason}",
      )
    }
  }
  let runtime : DurableRuntime = {
    outbox,
    gate: @semaphore.Semaphore(1),
    recovered_requires_session_present: persisted.any(record => record.ever_started),
    now_ms: durable_now_ms,
  }
  let deliveries = DeliveryBook::new()
  let recovered : Array[DeliveryHandle] = []
  for stored in persisted {
    let record : DeliveryRecord = {
      delivery_id: stored.delivery_id,
      packet_id: stored.packet_id,
      changed: @cond_var.Cond(),
      topic: Some(stored.topic),
      payload: Some(stored.payload),
      retain: stored.retain,
      phase: AwaitingReconnect("recovered from durable outbox"),
      attempts: stored.attempts,
      generation: None,
      ever_started: stored.ever_started,
      start_deadline: None,
      ack_deadline: None,
      terminal_cause: None,
      durable: Some({
        runtime,
        expires_at_ms: stored.expires_at_ms,
      }),
    }
    if deliveries.active.contains(record.delivery_id) ||
      deliveries.reserved.contains(record.packet_id) {
      raise DurableStoreSchema(
        "durable records contain duplicate delivery or packet identifiers",
      )
    }
    deliveries.active[record.delivery_id] = record
    deliveries.reserved[record.packet_id] = record
    deliveries.order.push(record)
    recovered.push({ record, })
  }
  let client = new_client(config, deliveries, Some(runtime))
  // Error unwinding first joins the task group, then settles through the
  // durable gate, and only the earlier outer errdefer closes SQLite.
  errdefer client.settle_deliveries(
    SessionLost("durable client scope ended"),
  )
  let result = @async.with_task_group(group => {
    let worker = group.spawn(() => client.supervise())
    client.worker = Some(worker)
    defer {
      client.stopped = true
      client.ready = false
      if client.session is Some(session) {
        session.abort("durable client scope ended")
      }
      worker.cancel()
      client.events.close(error=Closed)
      client.changed.broadcast()
    }
    client.wait_connected()
    let value = action(client, recovered)
    client.disconnect()
    value
  })
  client.settle_deliveries(SessionLost("durable client scope ended"))
  outbox.close()
  result
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

///| Effective limits for the currently ready physical generation.
pub fn Client::negotiated_settings(
  self : Client,
) -> NegotiatedSettings raise ClientError {
  if !self.ready { raise NotConnected }
  match self.negotiated {
    Some(value) => value
    None => raise NotConnected
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
    BrokerRejected(reason) => BrokerRejected(reason)
    ServerDisconnected(reason) => ServerDisconnected(reason)
    ReconnectExhausted(msg) => ReconnectExhausted(msg)
    DeliveryRejected(msg) => DeliveryRejected(msg)
    BrokerSessionLost(msg) => BrokerSessionLost(msg)
    DeliveryAttemptsExhausted(msg) => DeliveryAttemptsExhausted(msg)
    DurableStorage(error) => DurableStorage(error)
    DurableStoreBusy(msg) => DurableStorage(DurableStoreBusy(msg))
    DurableStoreFull(msg) => DurableStorage(DurableStoreFull(msg))
    DurableStoreReadOnly(msg) => DurableStorage(DurableStoreReadOnly(msg))
    DurableStoreCorrupt(msg) => DurableStorage(DurableStoreCorrupt(msg))
    DurableStoreIo(msg) => DurableStorage(DurableStoreIo(msg))
    DurableStoreSchema(msg) => DurableStorage(DurableStoreSchema(msg))
    DurableStoreIdentity(msg) => DurableStorage(DurableStoreIdentity(msg))
    DurableStoreLimit(msg) => DurableStorage(DurableStoreLimit(msg))
    DurableStoreState(msg) => DurableStorage(DurableStoreState(msg))
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
fn durable_store_error_of(error : Error) -> DurableStoreError? {
  try {
    raise error
  } catch {
    DurableStoreBusy(msg) => Some(DurableStoreBusy(msg))
    DurableStoreFull(msg) => Some(DurableStoreFull(msg))
    DurableStoreReadOnly(msg) => Some(DurableStoreReadOnly(msg))
    DurableStoreCorrupt(msg) => Some(DurableStoreCorrupt(msg))
    DurableStoreIo(msg) => Some(DurableStoreIo(msg))
    DurableStoreSchema(msg) => Some(DurableStoreSchema(msg))
    DurableStoreIdentity(msg) => Some(DurableStoreIdentity(msg))
    DurableStoreLimit(msg) => Some(DurableStoreLimit(msg))
    DurableStoreState(msg) => Some(DurableStoreState(msg))
    _ => None
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
  properties? : Array[@mqtt5.Property] = [],
  refresh? : ((Int) -> Bytes raise ClientError)? = None,
  packet_limit? : Int? = None,
  inflight_limit? : Int? = None,
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
    self.allocate(inflight_limit.unwrap_or(config.max_inflight))
  }
  let wire_limit = packet_limit.unwrap_or(config.max_packet_size)
  let bytes = encode_wire(config.protocol, packet(id), properties, wire_limit)
  let request : Pending = {
    id,
    kind,
    changed: @cond_var.Cond(),
    started: false,
    result: None,
    queued_at: @async.now(),
    refresh: refresh.map(builder => () => builder(id)),
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
  properties? : PublishProperties = PublishProperties::new(),
) -> Unit {
  ignore(self.publish_detailed(topic, payload, qos~, retain~, properties~))
}

///| Publish and retain the MQTT 5 broker acknowledgement reason.
pub async fn Client::publish_detailed(
  self : Client,
  topic : String,
  payload : Bytes,
  qos? : QoS = AtMostOnce,
  retain? : Bool = false,
  properties? : PublishProperties = PublishProperties::new(),
) -> PublishReceipt {
  if self.config.session_policy is ResumeSession && qos == AtLeastOnce {
    raise DeliveryRejected(
      "QoS 1 publish under ResumeSession requires submit_delivery",
    )
  }
  let session = self.active()
  let negotiated = self.negotiated_settings()
  if qos == AtLeastOnce && negotiated.maximum_qos == AtMostOnce {
    raise BrokerRejected({ code: 155, reason_string: Some("server Maximum QoS is 0"), })
  }
  if retain && !negotiated.retain_available {
    raise BrokerRejected({ code: 154, reason_string: Some("server Retain Available is 0"), })
  }
  if payload.length() > self.config.max_packet_size - 4 {
    raise ProtocolError("outgoing payload exceeds packet limit")
  }
  let owned = properties.owned()
  let expiry_at = owned.message_expiry_secs.map(seconds =>
    durable_now_ms() + seconds * 1000L
  )
  let make_packet = id => @codec.PublishPacket({
    topic,
    payload,
    qos: qos.wire(),
    message_id: if qos == AtMostOnce { None } else { Some(id) },
    retain,
    dup: false,
  })
  let wire_properties = publish_properties_wire(
    owned,
    expiry_at,
    durable_now_ms(),
  )
  let reply = session.request(
      self.config,
      if qos == AtMostOnce {
        Publish0
      } else {
        Publish1
      },
      make_packet,
      reason => self.abort_session(reason),
      properties=wire_properties,
      refresh=Some(fn(id) -> Bytes raise ClientError {
        encode_wire(
          self.config.protocol,
          make_packet(id),
          publish_properties_wire(owned, expiry_at, durable_now_ms()),
          negotiated.max_packet_size,
        )
      }),
      packet_limit=Some(negotiated.max_packet_size),
      inflight_limit=Some(negotiated.receive_maximum),
      on_unknown=() => self.note_unknown(),
    )
  match reply {
    Published(receipt) => receipt
    _ => raise ProtocolError("wrong publish completion")
  }
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
  if self.durable is Some(_) {
    raise DeliveryRejected(
      "durable client scopes require submit_durable_delivery",
    )
  }
  if !valid_delivery_id(delivery_id) {
    raise DeliveryRejected("delivery_id must contain 1..128 UTF-8 bytes")
  }
  if self.deliveries.active.contains(delivery_id) {
    raise DeliveryRejected("duplicate active delivery_id: \{delivery_id}")
  }
  let session = self.active()
  if payload.length() > self.config.max_packet_size - 4 {
    raise ProtocolError("outgoing payload exceeds packet limit")
  }
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
    start_deadline: None,
    ack_deadline: None,
    terminal_cause: None,
    durable: None,
  }
  // Encode before mutating ownership so an invalid topic/packet bound leaves no
  // active ID, packet reservation, or queue entry behind.
  let bytes = record.packet(self.config.max_packet_size)
  self.deliveries.active[delivery_id] = record
  self.deliveries.reserved[packet_id] = record
  self.deliveries.order.push(record)
  record.attach(
    session.generation,
    @async.now() + self.config.operation_timeout_ms.to_int64(),
  )
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
/// Commit one durable QoS 1 admission before it can become visible to the
/// network. `expires_at_ms` is an absolute Unix-millisecond deadline persisted
/// once; cancellation after commit leaves the stable ID inspectable.
pub async fn Client::submit_durable_delivery(
  self : Client,
  delivery_id : String,
  topic : String,
  payload : Bytes,
  expires_at_ms : Int64,
  retain? : Bool = false,
) -> DeliveryHandle {
  guard self.durable is Some(runtime) else {
    raise DeliveryRejected("submit_durable_delivery requires a durable client scope")
  }
  if !valid_delivery_id(delivery_id) {
    raise DeliveryRejected("delivery_id must contain 1..128 UTF-8 bytes")
  }
  if payload.length() > self.config.max_packet_size - 4 {
    raise ProtocolError("outgoing payload exceeds packet limit")
  }
  let now = (runtime.now_ms)()
  if expires_at_ms <= now {
    raise DeliveryRejected("durable delivery expiry must be in the future")
  }
  let attached : (DeliveryRecord, Session?) = try {
    runtime.transition(async fn() {
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
        start_deadline: None,
        ack_deadline: None,
        terminal_cause: None,
        durable: None,
      }
      // Reserve the MQTT identifier before the first SQLite suspension so an
      // ordinary request cannot claim it while admission commits.
      self.deliveries.reserved[packet_id] = record
      let mut admitted = false
      errdefer (if !admitted { self.deliveries.reserved.remove(packet_id) })
      ignore(record.packet(self.config.max_packet_size))
      // Occupy the inflight capacity before SQLite can suspend. Ordinary
      // requests do not share the durable FIFO gate, so the packet-ID
      // reservation alone would let one of them steal the last slot while the
      // durable COMMIT is pending.
      session.delivery_slots += 1
      let mut provisional_slot = true
      errdefer {
        if provisional_slot && session.alive && session.delivery_slots > 0 {
          session.delivery_slots -= 1
          session.delivery_changed.broadcast()
        }
      }
      let stored = runtime.outbox.admit(
        delivery_id,
        packet_id,
        topic,
        payload,
        retain,
        expires_at_ms,
        now,
      )
      admitted = true
      record.durable = Some({
        runtime,
        expires_at_ms: stored.expires_at_ms,
      })
      self.deliveries.active[delivery_id] = record
      self.deliveries.order.push(record)
      // The connection may disappear during the durable admission. The row and
      // handle remain admitted; a later replay owns attachment in that case.
      let current : Session? = match self.session {
        Some(current) if self.ready && current.alive &&
          current.generation == session.generation => Some(current)
        _ => None
      }
      match current {
        Some(current) => {
          runtime.outbox.record_attach(delivery_id)
          record.attach(
            current.generation,
            @async.now() + self.config.operation_timeout_ms.to_int64(),
          )
          current.deliveries[packet_id] = record
          // The provisional capacity reservation now belongs to this record.
          provisional_slot = false
          (record, Some(current))
        }
        None => {
          if session.alive && session.delivery_slots > 0 {
            session.delivery_slots -= 1
            session.delivery_changed.broadcast()
          }
          provisional_slot = false
          record.phase = AwaitingReconnect(
            "connection changed after durable admission",
          )
          record.changed.broadcast()
          (record, None)
        }
      }
    })
  } catch {
    error => {
      match durable_store_error_of(error) {
        Some(store_error) => {
          let wrapped = DurableStorage(store_error)
          self.record_stop(wrapped)
          self.stopped = true
          self.ready = false
          match self.session {
            Some(session) => session.abort_with(wrapped)
            None => ()
          }
          self.changed.broadcast()
          raise wrapped
        }
        None => raise error
      }
    }
  }
  let (record, generation) = attached
  // protect_from_cancel deliberately completes the storage+memory transition
  // with the task still marked cancelled. This checkpoint propagates that
  // cancellation before any optional queue wait while leaving stable ownership.
  @async.pause()
  match generation {
    Some(session) => {
      let bytes = record.packet(self.config.max_packet_size)
      try {
        session.enqueue_delivery(record, bytes)
      } catch {
        error => {
          if @async.is_cancellation_error(error) {
            raise error
          }
          // Admission is already committed. Generation teardown detaches this
          // record, and the supervisor retries it without rolling storage back.
        }
      }
    }
    None => ()
  }
  { record, }
}

///|
fn Client::finish_deliveries(self : Client, error : Error) -> Unit {
  // Durable settlement must serialize with ACK DELETE and other storage
  // transitions. Async owners call settle_deliveries after their task joins.
  if self.durable is Some(_) {
    return
  }
  self.finish_deliveries_now(error)
}

///|
fn Client::finish_deliveries_now(self : Client, error : Error) -> Unit {
  if self.deliveries.active.is_empty() {
    return
  }
  for record in self.deliveries.order {
    if !record.phase.terminal() && record.ever_started {
      self.unknown_outcomes += 1
    }
  }
  let cause = match error {
    // Scope shutdown is a normal close, while all supervisor terminal causes
    // already have a public ClientError variant.
    SessionLost(_) => Closed
    _ => client_error_of(error)
  }
  self.deliveries.finish_all(session_error_reason(error), cause)
}

///|
async fn Client::settle_deliveries(self : Client, error : Error) -> Unit {
  match self.durable {
    Some(runtime) =>
      runtime.transition(fn() {
        self.finish_deliveries_now(error)
      })
    None => self.finish_deliveries_now(error)
  }
}

///|
pub async fn Client::subscribe(
  self : Client,
  topics : Array[Subscription],
) -> Array[SubscriptionResult] {
  self.subscribe_detailed(topics).map(value => value.result)
}

///| Subscribe while preserving every numeric MQTT acknowledgement reason.
pub async fn Client::subscribe_detailed(
  self : Client,
  topics : Array[Subscription],
) -> Array[SubscriptionAcknowledgement] {
  self.subscribe_on(self.active(), topics.copy())
}

///|
async fn Client::subscribe_on(
  self : Client,
  session : Session,
  topics : Array[Subscription],
) -> Array[SubscriptionAcknowledgement] {
  match
    session.request(self.config, Subscribe(topics), id => {
      @codec.SubscribePacket({
        message_id: id,
        topics: topics.map(t => { topic: t.topic, qos: t.qos.wire(), }),
      })
    }, reason => self.abort_session(reason), on_unknown=() => self.note_unknown()) {
    SubscriptionsDetailed(results) => results
    _ => raise ProtocolError("wrong subscribe completion")
  }
}

///|
pub async fn Client::unsubscribe(self : Client, topics : Array[String]) -> Unit {
  ignore(self.unsubscribe_detailed(topics))
}

///| Unsubscribe while preserving numeric MQTT 5 reason codes.
pub async fn Client::unsubscribe_detailed(
  self : Client,
  topics : Array[String],
) -> Array[BrokerReason] {
  let session = self.active()
  let topics = topics.copy()
  match session.request(self.config, Unsubscribe(topics), id => {
      @codec.UnsubscribePacket({ message_id: id, topics, })
    }, reason => self.abort_session(reason), on_unknown=() => self.note_unknown()) {
    Unsubscriptions(reasons) => reasons
    _ => raise ProtocolError("wrong unsubscribe completion")
  }
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
  self.settle_deliveries(SessionLost("client disconnected"))
}
