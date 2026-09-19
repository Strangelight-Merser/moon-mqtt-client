///|
priv enum RequestKind {
  Publish0
  Publish1
  Subscribe(Array[Subscription])
  Unsubscribe(Array[String])
  Disconnect
}

///|
priv enum Reply {
  Done
  Published(PublishReceipt)
  SubscriptionsDetailed(Array[SubscriptionAcknowledgement])
  Unsubscriptions(Array[BrokerReason])
}

///|
priv struct Pending {
  id : Int
  kind : RequestKind
  changed : @cond_var.Cond
  mut started : Bool
  mut result : Result[Reply, ClientError]?
  /// Monotonic time the request was accepted, used as the start of the
  /// `operation_timeout_ms` budget (queue time counts).
  queued_at : Int64
  refresh : (() -> Bytes raise ClientError)?
}

///|
/// One queued write. Business requests carry a `pending` entry; protocol
/// control packets do not. `is_ping` lets the write loop start the PINGRESP
/// clock exactly when the PINGREQ write completes.
priv struct Outgoing {
  bytes : Bytes
  pending : Pending?
  delivery : DeliveryRecord?
  is_ping : Bool
}

///|
priv struct Session {
  generation : Int
  reader : &@io.Reader
  writer : &@io.Writer
  close_transport : () -> Unit
  /// Business writes. Bounded by `Config::send_capacity`.
  outgoing : @aqueue.Queue[Outgoing]
  /// Reserved protocol slots. Bounded by `Config::control_capacity` and never
  /// occupied by business traffic, so a saturated business queue cannot starve
  /// PUBACK/PINGREQ/DISCONNECT.
  control_queue : @aqueue.Queue[Outgoing]
  /// Occupancy mirrors maintained by the producers/consumer. `@aqueue.Queue`
  /// exposes no length, and `Client::stats` must report occupancy without
  /// consuming items.
  mut business_depth : Int
  mut control_depth : Int
  pending : Map[Int, Pending]
  /// Recoverable deliveries attached to this physical generation. The client
  /// owns their lifetime; this map only routes PUBACK and generation teardown.
  deliveries : Map[Int, DeliveryRecord]
  /// Shared logical-session reservations, including deliveries detached while
  /// reconnecting. Ordinary request allocation must always skip them.
  reserved_deliveries : Map[Int, DeliveryRecord]
  mut delivery_slots : Int
  mut qos1_outstanding : Int
  outgoing_limit : Int
  receive_maximum : Int
  mut parked_business : Outgoing?
  delivery_changed : @cond_var.Cond
  mut next_id : Int
  mut alive : Bool
  mut last_write : Int64
  /// Heartbeat state in one field: `None` is idle, -1 is queued/writing, -2 is
  /// an early PINGRESP waiting for write completion, and a non-negative value
  /// is the completed-write timestamp. The native async runtime obtains
  /// `@async.now()` from an unsigned millisecond-since-epoch source, so these
  /// private negative sentinels cannot overlap a runtime timestamp.
  mut ping_sent : Int64?
  early_messages : Array[Message]
  /// First error that ended this generation. Keeping the typed value lets the
  /// supervisor distinguish terminal protocol failures from retryable I/O loss.
  mut failure_reason : Error?
}

///|
fn Pending::finish(self : Pending, result : Result[Reply, ClientError]) -> Unit {
  if self.result is None {
    self.result = Some(result)
    self.changed.broadcast()
  }
}

///|
fn Session::abort(self : Session, reason : String) -> Unit {
  self.abort_with(SessionLost(reason))
}

///|
fn session_error_reason(error : Error) -> String {
  match error {
    SessionLost(reason) => reason
    InvalidConfig(reason) => reason
    ProtocolError(reason) => reason
    Backpressure(reason) => reason
    NotSent(reason) => reason
    OutcomeUnknown(reason) => reason
    ConnectionRefused(reason) => reason
    BrokerRejected(reason) =>
      "broker rejected request with reason code \{reason.code}"
    ServerDisconnected(reason) =>
      "server disconnected with reason code \{reason.code}"
    ReconnectExhausted(reason) => reason
    DeliveryRejected(reason) => reason
    BrokerSessionLost(reason) => reason
    DeliveryAttemptsExhausted(reason) => reason
    DurableStorage(error) => @debug.to_string(error)
    DeliveryAttemptTimeout(reason) => reason
    OperationTimeout(reason) => reason
    WriteTimeout(reason) => reason
    PingResponseTimeout(reason) => reason
    TlsFailure(reason) => reason
    _ => error.to_string()
  }
}

///|
/// End the generation with its original typed cause. Pending operations still
/// receive the stable string form required by `NotSent`/`OutcomeUnknown`.
fn Session::abort_with(self : Session, error : Error) -> Unit {
  if self.alive {
    self.alive = false
    if self.failure_reason is None {
      self.failure_reason = Some(error)
    }
    let reason = session_error_reason(error)
    (self.close_transport)()
    // `clear=true` discards every queued item, so the occupancy mirrors must be
    // zeroed here too. Otherwise `Client::stats` would keep reporting a
    // discarded backlog as if it were still queued.
    self.outgoing.close(error=Closed, clear=true)
    self.control_queue.close(error=Closed, clear=true)
    self.business_depth = 0
    self.control_depth = 0
    self.ping_sent = None
    for _, request in self.pending {
      // `started` means the bytes reached the socket write call, so the peer
      // may have executed the request even though no ACK arrived.
      let error = if request.started {
        OutcomeUnknown(reason)
      } else {
        NotSent(reason)
      }
      request.finish(Err(error))
    }
    self.pending.clear()
    for _, delivery in self.deliveries {
      delivery.detach(self.generation, reason)
    }
    self.deliveries.clear()
    self.delivery_slots = 0
    self.qos1_outstanding = 0
    self.parked_business = None
    self.delivery_changed.broadcast()
  }
}

///|
fn Session::allocate(
  self : Session,
  max_inflight : Int,
) -> Int raise ClientError {
  if !self.alive {
    raise NotConnected
  }
  if self.pending.length() + self.delivery_slots >= max_inflight {
    raise Backpressure("inflight limit reached")
  }
  for _ in 0..<65535 {
    let id = self.next_id
    self.next_id = if id == 65535 { 1 } else { id + 1 }
    if !self.pending.contains(id) && !self.reserved_deliveries.contains(id) {
      return id
    }
  }
  raise Backpressure("all packet identifiers are occupied")
}

///|
/// Enqueue one protocol control packet into the reserved slots. A full control
/// queue is a terminal condition: dropping a PUBACK or a PINGREQ would silently
/// break the protocol, so the caller terminates the session instead.
fn Session::control(
  self : Session,
  packet : @codec.Packet,
  limit : Int,
  is_ping? : Bool = false,
) -> Unit raise ClientError {
  if !self.alive {
    raise NotConnected
  }
  let encoded = encode(packet, limit)
  let accepted = self.control_queue.try_put({
    bytes: encoded,
    pending: None,
    delivery: None,
    is_ping,
  }) catch {
    _ => false
  }
  if accepted {
    self.control_depth += 1
  } else {
    raise ProtocolError(
      "control queue exhausted; terminating instead of dropping a control packet",
    )
  }
}

///|
/// Protocol-aware control encoding. MQTT 5 control packets always pass through
/// the MQTT 5 encoder; the legacy helper remains for MQTT 3.1.1 tests.
fn Session::control_wire(
  self : Session,
  packet : @codec.Packet,
  config : Config,
  is_ping? : Bool = false,
) -> Unit raise ClientError {
  if !self.alive {
    raise NotConnected
  }
  let encoded = encode_wire(
    config.protocol,
    packet,
    [],
    config.max_packet_size.min(self.outgoing_limit),
  )
  let accepted = self.control_queue.try_put({
    bytes: encoded,
    pending: None,
    delivery: None,
    is_ping,
  }) catch {
    _ => false
  }
  if accepted {
    self.control_depth += 1
  } else {
    raise ProtocolError(
      "control queue exhausted; terminating instead of dropping a control packet",
    )
  }
}

///|
/// Choose the next write. Protocol control packets take priority over business
/// traffic so heartbeats and acknowledgements cannot be delayed behind a large
/// business backlog. Both queues are empty exactly when this returns `None`.
fn Session::next_outgoing(self : Session) -> Outgoing? {
  // `try_get` raises once the queue has been closed by `abort`. Reaching that
  // state is normal shutdown, not a fault: the write loop must return quietly so
  // the supervisor can reconnect. Without this, closing the queue while the
  // write loop sleeps would fail the task group and hang the reconnection path.
  let control = self.control_queue.try_get() catch { _ => return None }
  match control {
    Some(item) => {
      if self.control_depth > 0 {
        self.control_depth -= 1
      }
      return Some(item)
    }
    None => ()
  }
  let business = match self.parked_business {
    Some(item) => Some(item)
    None => self.outgoing.try_get() catch { _ => return None }
  }
  match business {
    Some(item) => {
      let requires_credit = item.delivery is Some(_) ||
        (item.pending is Some(request) && request.kind is Publish1)
      if requires_credit && self.qos1_outstanding >= self.receive_maximum {
        self.parked_business = Some(item)
        return None
      }
      self.parked_business = None
      if self.business_depth > 0 {
        self.business_depth -= 1
      }
      // A replay waiting for a bounded business slot can now try admission.
      // The mirror describes actual queue occupancy, never blocked producers.
      self.delivery_changed.broadcast()
      Some(item)
    }
    None => None
  }
}

///|
/// Queue an already-admitted replay without widening the configured queue.
async fn Session::enqueue_delivery(
  self : Session,
  delivery : DeliveryRecord,
  bytes : Bytes,
) -> Unit {
  while self.alive &&
        delivery.generation == Some(self.generation) &&
        !delivery.phase.terminal() {
    let accepted = self.enqueue_business({
      bytes,
      pending: None,
      delivery: Some(delivery),
      is_ping: false,
    }) catch {
      _ => false
    }
    if accepted {
      self.business_depth += 1
      return
    }
    self.delivery_changed.wait()
  }
  match self.failure_reason {
    Some(error) => raise error
    None => raise Closed
  }
}

///|
/// Mark a request as started. From this point the bytes may reach the peer, so
/// a later failure is an unknown outcome, never "not sent".
fn mark_started(request : Pending) -> Unit {
  if !request.started {
    request.started = true
  }
}

///|
/// Negative values are private heartbeat states. Runtime timestamps are
/// sourced from unsigned milliseconds since epoch, so the existing optional
/// field can distinguish these states without changing the Session shape.
fn ping_write_pending() -> Int64 {
  -1L
}

///|
fn ping_responded_while_writing() -> Int64 {
  -2L
}

///|
fn Session::queue_ping(self : Session, config : Config) -> Unit raise ClientError {
  if self.ping_sent is Some(_) {
    return
  }
  self.ping_sent = Some(ping_write_pending())
  errdefer {
    self.ping_sent = None
  }
  self.control_wire(@codec.PingreqPacket, config, is_ping=true)
}

///|
fn Session::receive_pingresp(self : Session) -> Unit {
  match self.ping_sent {
    Some(state) if state == ping_write_pending() ||
      state == ping_responded_while_writing() =>
      self.ping_sent = Some(ping_responded_while_writing())
    Some(_) => self.ping_sent = None
    None => ()
  }
}

///|
/// Perform the single socket write for one queued item.
///
/// - a cancellation propagates unchanged (scope shutdown),
/// - a write that exceeds its write timeout aborts the session and stops the
///   loop: the transport is in an unknown state and must be re-established,
/// - an expired operation budget aborts the session and stops the loop so a
///   queued request can be reported as `NotSent` instead of waiting forever.
async fn Session::write_one(
  self : Session,
  item : Outgoing,
  operation_timeout_ms : Int,
  write_timeout_ms : Int,
  reconnect_attempts : Int,
) -> Bool {
  // Snapshot DUP before durable possible-write persistence changes the record.
  let was_started = match item.delivery {
    Some(delivery) => delivery.ever_started
    None => false
  }
  let timed_out_before_write = match item.pending {
    Some(request) =>
      operation_timeout_ms.to_int64() <= @async.now() - request.queued_at
    None => false
  }
  if timed_out_before_write {
    // Reaching here means the caller-side operation timeout has not aborted yet
    // (or the request was queued after it was already late). Stop the loop so
    // the request is reported as not sent instead of waiting forever.
    self.abort(
      "operation timeout of \{operation_timeout_ms} ms expired before the request was written",
    )
    return false
  }
  match item.pending {
    Some(request) =>
      if request.result is Some(_) {
        // The caller abandoned this request (cancellation) while it was still
        // queued. It must never be written: it is already settled as not sent.
        return true
      }
    None => ()
  }
  match item.delivery {
    Some(delivery) => {
      if delivery.phase.terminal() ||
        delivery.generation != Some(self.generation) {
        return true
      }
      if delivery.start_deadline is Some(deadline) && @async.now() >= deadline {
        let detail = "delivery \{delivery.delivery_id} packet \{delivery.packet_id} did not begin its socket write within operation_timeout_ms=\{operation_timeout_ms} on attempt \{delivery.attempts}"
        let error : Error = if delivery.attempts > reconnect_attempts {
          DeliveryAttemptsExhausted(detail)
        } else {
          DeliveryAttemptTimeout(detail)
        }
        self.abort_with(error)
        return false
      }
      match delivery.durable {
        Some(meta) => {
          let original_start_deadline = delivery.start_deadline
          let transition = meta.runtime.transition(async fn() {
            let transition_now = (meta.runtime.now_ms)()
            if delivery.message_expiry_at_ms is Some(deadline) &&
              transition_now >= deadline {
              let error = DeliveryRejected(
                "durable delivery \{delivery.delivery_id} Message Expiry elapsed before its first possible write",
              )
              meta.runtime.outbox.block(
                delivery.delivery_id,
                session_error_reason(error),
              )
              delivery.finish_terminal(session_error_reason(error), error)
              return Some(error)
            }
            match
              meta.runtime.outbox.expire_one(
                delivery.delivery_id,
                transition_now,
              ) {
              ExpiryStillPending => {
                if !delivery.ever_started {
                  meta.runtime.outbox.record_may_write(delivery.delivery_id)
                }
                delivery.mark_started()
                None
              }
              ExpiryRemoved => {
                let error = DeliveryRejected(
                  "durable delivery \{delivery.delivery_id} expired before its first possible write",
                )
                delivery.finish_terminal(session_error_reason(error), error)
                Some(error)
              }
              ExpiryBlocked => {
                let error = DeliveryRejected(
                  "durable delivery \{delivery.delivery_id} expired after a possible write",
                )
                delivery.finish_terminal(session_error_reason(error), error)
                Some(error)
              }
            }
          }) catch {
            error => {
              if @async.is_cancellation_error(error) {
                raise error
              }
              match durable_store_error_of(error) {
                Some(store_error) => Some(DurableStorage(store_error))
                None => Some(client_error_of(error))
              }
            }
          }
          match transition {
            Some(error) => {
              self.abort_with(error)
              return false
            }
            None => ()
          }
          // The durable transition can suspend while another task ends this
          // physical generation. A conservative possible-write bit may remain,
          // but dead-generation bytes must never reach its socket.
          if !self.alive ||
            delivery.generation != Some(self.generation) ||
            delivery.phase.terminal() {
            return false
          }
          // Both SQLite calls above may suspend. Recheck immutable wall expiry
          // and the original monotonic queue budget after the final COMMIT and
          // immediately before the socket write.
          let wall_now = (meta.runtime.now_ms)()
          if wall_now >= meta.expires_at_ms {
            let error : Error = meta.runtime.transition(async fn() {
              ignore(
                meta.runtime.outbox.expire_one(delivery.delivery_id, wall_now),
              )
              let expired = DeliveryRejected(
                "durable delivery \{delivery.delivery_id} expired after possible-write persistence and before socket write",
              )
              delivery.finish_terminal(session_error_reason(expired), expired)
              expired
            }) catch {
              store_error => {
                if @async.is_cancellation_error(store_error) {
                  raise store_error
                }
                match durable_store_error_of(store_error) {
                  Some(value) => DurableStorage(value)
                  None => client_error_of(store_error)
                }
              }
            }
            self.abort_with(error)
            return false
          }
          if original_start_deadline is Some(deadline) &&
            @async.now() >= deadline {
            let detail = "delivery \{delivery.delivery_id} packet \{delivery.packet_id} exceeded its pre-write operation budget while persisting durable possible-write state on attempt \{delivery.attempts}"
            let error : Error = if delivery.attempts > reconnect_attempts {
              DeliveryAttemptsExhausted(detail)
            } else {
              DeliveryAttemptTimeout(detail)
            }
            self.abort_with(error)
            return false
          }
        }
        None => ()
      }
    }
    None => ()
  }
  try {
    let bytes = match item.delivery {
      Some(delivery) => {
        let bytes = delivery.packet(dup=Some(was_started))
        // For an in-memory delivery, packet construction refreshes Message
        // Expiry and can still reject before any socket side effect is
        // possible. Mark it started only after that refresh succeeds. Durable
        // deliveries keep their earlier COMMIT-before-write transition above.
        if delivery.durable is None {
          delivery.mark_started()
        }
        bytes
      }
      None =>
        match item.pending {
          Some(request) =>
            match request.refresh {
              Some(refresh) => refresh()
              None => item.bytes
            }
          None => item.bytes
        }
    }
    if item.pending is Some(request) {
      mark_started(request)
    }
    if item.delivery is Some(_) ||
      (item.pending is Some(request) && request.kind is Publish1) {
      self.qos1_outstanding += 1
    }
    @async.with_timeout(write_timeout_ms, () => self.writer.write(bytes))
  } catch {
    error => {
      if @async.is_cancellation_error(error) {
        raise error
      }
      if self.alive {
        match error {
          @async.TimeoutError =>
            self.abort_with(
              WriteTimeout(
                "socket write exceeded write_timeout_ms=\{write_timeout_ms}",
              ),
            )
          _ => self.abort_with(error)
        }
        return false
      }
      return false
    }
  }
  // Teardown can close a generation while a custom/OS writer is still inside
  // `write`. A later return must not publish fresh state into that dead
  // generation.
  if !self.alive {
    return false
  }
  self.last_write = @async.now()
  if item.delivery is Some(delivery) {
    delivery.mark_written(self.last_write + operation_timeout_ms.to_int64())
    self.delivery_changed.broadcast()
  }
  if item.is_ping {
    match self.ping_sent {
      Some(state) if state == ping_responded_while_writing() =>
        // The peer observed the bytes before the writer future completed.
        // Keep the slot occupied until this point, then finish the exchange.
        self.ping_sent = None
      Some(state) if state == ping_write_pending() =>
        // PINGRESP timeout starts only after the PINGREQ write completed.
        self.ping_sent = Some(self.last_write)
      Some(_) => ()
      // Production PINGREQ writes are admitted only by queue_ping. If no
      // exchange remains, leave the heartbeat idle instead of reviving it.
      None => ()
    }
  }
  match item.pending {
    Some(request) =>
      match request.kind {
        Publish0 | Disconnect => {
          self.pending.remove(request.id)
          request.finish(
            Ok(if request.kind is Publish0 { Published(Written) } else { Done }),
          )
          // No MQTT control packet may follow DISCONNECT, including a queued
          // heartbeat or PUBACK while graceful framing shutdown is pending.
          if request.kind is Disconnect {
            return false
          }
        }
        _ => ()
      }
    None => ()
  }
  true
}

///|
priv suberror DeliveryAttemptTimeout {
  DeliveryAttemptTimeout(String)
}

///|
/// One watchdog per physical generation. A waiter never owns this timer.
async fn Session::expire_durable_delivery(
  self : Session,
  delivery : DeliveryRecord,
) -> Error? {
  guard delivery.durable is Some(meta) else { return None }
  meta.runtime.transition(async fn() {
    // The wall-clock observation which selected this record happened before
    // waiting for the FIFO gate. An ACK or generation teardown may have won
    // the gate in the meantime, so both ownership and time are authoritative
    // only after the gate is held.
    if !self.alive ||
      delivery.generation != Some(self.generation) ||
      delivery.phase.terminal() ||
      !self.deliveries.contains(delivery.packet_id) {
      return None
    }
    let now_ms = (meta.runtime.now_ms)()
    if now_ms < meta.expires_at_ms {
      return None
    }
    let error : Error? = match
      meta.runtime.outbox.expire_one(delivery.delivery_id, now_ms) {
      // A backwards wall-clock step, or an already-settled owner observed
      // before the checks above, is not a protocol or storage failure.
      ExpiryStillPending => None
      ExpiryRemoved =>
        Some(
          DeliveryRejected(
            "durable delivery \{delivery.delivery_id} expired before its first possible write",
          ),
        )
      ExpiryBlocked =>
        Some(
          DeliveryRejected(
            "durable delivery \{delivery.delivery_id} expired after a possible write",
          ),
        )
    }
    match error {
      Some(cause) => {
        // Persisted expiry and the observable handle terminal state share the
        // gate. A queued ACK can therefore win entirely or observe this
        // already-recorded first cause, never a half-settled delivery.
        delivery.finish_terminal(
          session_error_reason(cause),
          client_error_of(cause),
        )
        self.abort_with(cause)
      }
      None => ()
    }
    error
  }) catch {
    store_error => {
      if @async.is_cancellation_error(store_error) {
        raise store_error
      }
      match durable_store_error_of(store_error) {
        Some(error) => Some(DurableStorage(error))
        None => Some(client_error_of(store_error))
      }
    }
  }
}

///|
/// One watchdog per physical generation. A waiter never owns this timer.
async fn Session::delivery_watchdog(self : Session, config : Config) -> Unit {
  let tick = (config.operation_timeout_ms / 4).clamp(min=10, max=250)
  while self.alive {
    @async.sleep(tick)
    let now = @async.now()
    for _, delivery in self.deliveries {
      if delivery.durable is Some(meta) &&
        (meta.runtime.now_ms)() >= meta.expires_at_ms {
        match self.expire_durable_delivery(delivery) {
          Some(error) => {
            // Successful expiry already recorded and aborted under the gate;
            // storage failures still need to stop this generation here.
            self.abort_with(error)
            raise error
          }
          None => ()
        }
      }
      let detail : String? = match delivery.start_deadline {
        Some(deadline) if now >= deadline =>
          Some(
            "delivery \{delivery.delivery_id} packet \{delivery.packet_id} did not begin its socket write within operation_timeout_ms=\{config.operation_timeout_ms} on attempt \{delivery.attempts}",
          )
        _ =>
          match delivery.ack_deadline {
            Some(deadline) if now >= deadline =>
              Some(
                "delivery \{delivery.delivery_id} packet \{delivery.packet_id} did not receive PUBACK in operation_timeout_ms=\{config.operation_timeout_ms} on attempt \{delivery.attempts}",
              )
            _ => None
          }
      }
      if detail is Some(detail) {
        let error : Error = if delivery.attempts > config.reconnect_attempts {
          DeliveryAttemptsExhausted(detail)
        } else {
          DeliveryAttemptTimeout(detail)
        }
        self.abort_with(error)
        raise error
      }
    }
  }
}

///|
async fn Session::write_loop(
  self : Session,
  operation_timeout_ms : Int,
  write_timeout_ms : Int,
  reconnect_attempts : Int,
) -> Unit {
  while self.alive {
    match self.next_outgoing() {
      Some(item) =>
        if !self.write_one(
            item, operation_timeout_ms, write_timeout_ms, reconnect_attempts,
          ) {
          return
        }
      None =>
        // Neither queue has work: stay cancellable with the session task group.
        @async.sleep(1)
    }
  }
}

///|
async fn Session::heartbeat(
  self : Session,
  config : Config,
  keep_alive_secs? : Int? = None,
) -> Unit {
  let keep_alive = keep_alive_secs.unwrap_or(config.keep_alive_secs)
  if keep_alive == 0 {
    // Remains cancellable with the surrounding session task group.
    while self.alive {
      @async.sleep(60000)
    }
    return
  }
  let interval = keep_alive * 1000
  let tick = (interval / 4).clamp(min=10, max=250)
  while self.alive {
    @async.sleep(tick)
    let now = @async.now()
    match self.ping_sent {
      Some(sent) =>
        if sent >= 0L &&
          now - sent >= config.ping_response_timeout_ms.to_int64() {
          raise PingResponseTimeout(
            "no PINGRESP within \{config.ping_response_timeout_ms} ms of the completed PINGREQ write",
          )
        }
      None =>
        if now - self.last_write >= (interval / 2).to_int64() {
          self.queue_ping(config)
        }
    }
  }
}

///|
/// The parked FIFO head still occupies a bounded business slot.
fn Session::enqueue_business(self : Session, item : Outgoing) -> Bool raise {
  match self.outgoing.kind {
    @aqueue.Blocking(limit) if self.business_depth >= limit => false
    _ => self.outgoing.try_put(item)
  }
}

///|
fn Session::return_publish_credit(self : Session) -> Unit {
  if self.qos1_outstanding > 0 {
    self.qos1_outstanding -= 1
  }
  self.delivery_changed.broadcast()
}
