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
  Subscriptions(Array[SubscriptionResult])
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
  delivery_changed : @cond_var.Cond
  mut next_id : Int
  mut alive : Bool
  mut last_write : Int64
  /// Set by the write loop only after PINGREQ has been fully written, so the
  /// PINGRESP clock never starts while the ping is still queued.
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
    ReconnectExhausted(reason) => reason
    DeliveryRejected(reason) => reason
    BrokerSessionLost(reason) => reason
    DeliveryAttemptsExhausted(reason) => reason
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
  let business = self.outgoing.try_get() catch { _ => return None }
  match business {
    Some(item) => {
      if self.business_depth > 0 {
        self.business_depth -= 1
      }
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
  self.business_depth += 1
  errdefer {
    if self.business_depth > 0 {
      self.business_depth -= 1
    }
  }
  self.outgoing.put({
    bytes,
    pending: None,
    delivery: Some(delivery),
    is_ping: false,
  })
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
) -> Bool {
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
      delivery.mark_started()
    }
    None => ()
  }
  if item.pending is Some(request) {
    mark_started(request)
  }
  try {
    @async.with_timeout(write_timeout_ms, () => self.writer.write(item.bytes))
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
  self.last_write = @async.now()
  if item.delivery is Some(delivery) {
    delivery.mark_written(
      self.last_write + operation_timeout_ms.to_int64(),
    )
    self.delivery_changed.broadcast()
  }
  if item.is_ping {
    // PINGRESP timer starts here: after the PINGREQ write completed.
    self.ping_sent = Some(self.last_write)
  }
  match item.pending {
    Some(request) =>
      match request.kind {
        Publish0 | Disconnect => {
          self.pending.remove(request.id)
          request.finish(Ok(Done))
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
async fn Session::delivery_watchdog(self : Session, config : Config) -> Unit {
  let tick = (config.operation_timeout_ms / 4).clamp(min=10, max=250)
  while self.alive {
    @async.sleep(tick)
    let now = @async.now()
    for _, delivery in self.deliveries {
      if delivery.ack_deadline is Some(deadline) && now >= deadline {
        let detail =
          "delivery \{delivery.delivery_id} packet \{delivery.packet_id} did not receive PUBACK in operation_timeout_ms=\{config.operation_timeout_ms} on attempt \{delivery.attempts}"
        let error : Error = if delivery.attempts >= config.reconnect_attempts + 1 {
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
) -> Unit {
  while self.alive {
    match self.next_outgoing() {
      Some(item) =>
        if !self.write_one(item, operation_timeout_ms, write_timeout_ms) {
          return
        }
      None => {
        // Neither queue has work: stay cancellable with the session task group.
        @async.sleep(1)
      }
    }
  }
}

///|
async fn Session::heartbeat(self : Session, config : Config) -> Unit {
  if config.keep_alive_secs == 0 {
    // Remains cancellable with the surrounding session task group.
    while self.alive {
      @async.sleep(60000)
    }
    return
  }
  let interval = config.keep_alive_secs * 1000
  let tick = (interval / 4).clamp(min=10, max=250)
  while self.alive {
    @async.sleep(tick)
    let now = @async.now()
    match self.ping_sent {
      Some(sent) =>
        if now - sent >= config.ping_response_timeout_ms.to_int64() {
          raise PingResponseTimeout(
            "no PINGRESP within \{config.ping_response_timeout_ms} ms of the completed PINGREQ write",
          )
        }
      None =>
        if now - self.last_write >= (interval / 2).to_int64() {
          // `ping_sent` is only set after the PINGREQ has left the write loop,
          // so a queued ping can never be mistaken for a lost PINGRESP.
          self.control(@codec.PingreqPacket, config.max_packet_size, is_ping=true)
        }
    }
  }
}
