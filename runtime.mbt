///|
/// Internal protocol-neutral events; MQTT 5 is decoded by its own codec first.
priv enum RuntimePacket {
  RuntimePublish(@codec.Publish, PublishProperties)
  RuntimePuback(Int, BrokerReason)
  RuntimeSuback(Int, Array[Int], BrokerReason)
  RuntimeUnsuback(Int, Array[Int], BrokerReason)
  RuntimePingresp
  RuntimeDisconnect(BrokerReason)
  RuntimeUnexpected
}

///|
fn normalize_packet(packet : IncomingPacket) -> RuntimePacket {
  match packet {
    Incoming311(@codec.PublishPacket(value)) =>
      RuntimePublish(value, PublishProperties::empty())
    Incoming311(@codec.PubackPacket(value)) =>
      RuntimePuback(value.message_id, { code: 0, reason_string: None, })
    Incoming311(@codec.SubackPacket(value)) => {
      let reasons = value.return_codes.map(code => {
        match code {
          @codec.SuccessQoS0 => 0
          @codec.SuccessQoS1 => 1
          @codec.SuccessQoS2 => 2
          @codec.Failure => 128
        }
      })
      RuntimeSuback(value.message_id, reasons, { code: 0, reason_string: None, })
    }
    Incoming311(@codec.UnsubackPacket(value)) =>
      RuntimeUnsuback(value.message_id, [], { code: 0, reason_string: None, })
    Incoming311(@codec.PingrespPacket) => RuntimePingresp
    Incoming5(@mqtt5.Publish(value, properties)) =>
      RuntimePublish(value, publish_properties_public(properties))
    Incoming5(@mqtt5.Puback(id, code, properties)) =>
      RuntimePuback(id, broker_reason(code, properties))
    Incoming5(@mqtt5.Suback(id, reasons, properties)) =>
      RuntimeSuback(id, reasons, broker_reason(0, properties))
    Incoming5(@mqtt5.Unsuback(id, reasons, properties)) =>
      RuntimeUnsuback(id, reasons, broker_reason(0, properties))
    Incoming5(@mqtt5.Pingresp) => RuntimePingresp
    Incoming5(@mqtt5.Disconnect(code, properties)) =>
      RuntimeDisconnect(broker_reason(code, properties))
    _ => RuntimeUnexpected
  }
}

///|
fn negotiated_settings(
  config : Config,
  properties : Array[@mqtt5.Property],
) -> NegotiatedSettings {
  let mut receive = config.max_inflight
  let mut packet = config.max_packet_size
  let mut qos = AtLeastOnce
  let mut retain = true
  let mut keep_alive = config.keep_alive_secs
  let mut expiry = config.session_expiry_secs
  for property in properties {
    match property {
      @mqtt5.ReceiveMaximum(value) => receive = receive.min(value)
      @mqtt5.MaximumPacketSize(value) =>
        packet = packet.min(value.min(2147483647L).to_int())
      @mqtt5.MaximumQoS(0) => qos = AtMostOnce
      @mqtt5.RetainAvailable(0) => retain = false
      @mqtt5.ServerKeepAlive(value) => keep_alive = value
      @mqtt5.SessionExpiry(value) => expiry = value
      _ => ()
    }
  }
  {
    receive_maximum: receive,
    max_packet_size: packet,
    maximum_qos: qos,
    retain_available: retain,
    keep_alive_secs: keep_alive,
    session_expiry_secs: expiry,
  }
}

///|
/// Exchange MQTT CONNECT/CONNACK over an established transport. Once TLS is
/// established, socket EOF/reset and TLS-layer errors during this exchange are
/// TLS transport failures. MQTT decoding errors, timeouts and cancellation keep
/// their original type.
async fn mqtt_connect_exchange(
  reader : &@io.Reader,
  writer : &@io.Writer,
  connect_bytes : Bytes,
  limit : Int,
  encrypted : Bool,
  protocol? : ProtocolVersion = Mqtt311,
) -> IncomingPacket {
  try {
    writer.write(connect_bytes)
    read_wire_packet(reader, protocol, limit)
  } catch {
    error => {
      if !encrypted {
        raise error
      }
      match error {
        @io.ReaderClosed | @tls.ConnectionClosed =>
          raise TlsFailure("TLS transport closed during MQTT connect")
        @tls.TlsError(message) => raise TlsFailure(message)
        @os_error.OSError(_) => raise TlsFailure(@tls.describe_tls_error(error))
        _ => raise error
      }
    }
  }
}

///|
fn websocket_handshake_error(error : Error, encrypted : Bool) -> Error {
  match error {
    @ws.InvalidHandshake(reason) =>
      ProtocolError("WebSocket handshake failed: \{reason}")
    @io.ReaderClosed | @tls.ConnectionClosed if encrypted =>
      TlsFailure("TLS transport closed during WebSocket handshake")
    @tls.TlsError(message) if encrypted => TlsFailure(message)
    @os_error.OSError(_) if encrypted =>
      TlsFailure(@tls.describe_tls_error(error))
    _ => error
  }
}

///|

///|
async fn Client::dial(self : Client) -> (Session, Bool, NegotiatedSettings) {
  @async.with_timeout(self.config.connect_timeout_ms, () => {
    let tcp = @socket.Tcp::connect_to_host(
      self.config.host[:],
      port=self.config.port,
    )
    errdefer tcp.close()
    let (stream_reader, stream_writer, close_stream) : (
      &@io.Reader,
      &@io.Writer,
      () -> Unit,
    ) = match self.config.tls {
      Plain => (tcp, tcp, () => tcp.close())
      mode => {
        let trust = match mode {
          SystemRoots => @tls.SystemRoot
          CustomCA(path) => @tls.CustomPemFile(path)
          Plain => @tls.SystemRoot
        }
        let tls = try {
          match self.config.client_identity {
            Some(id) =>
              @tls.Tls::client(tcp, host=self.config.host, trust~, identity={
                certificate_chain_file: id.certificate_chain_file,
                private_key_file: id.private_key_file,
              })
            None => @tls.Tls::client(tcp, host=self.config.host, trust~)
          }
        } catch {
          err => {
            let msg = @tls.describe_tls_error(err)
            if msg.contains("client identity:") {
              raise InvalidConfig(msg)
            }
            raise TlsFailure(msg)
          }
        }
        (
          tls,
          tls,
          () => {
            tls.close()
            tcp.close()
          },
        )
      }
    }
    errdefer close_stream()
    let (reader, writer, close_transport) : (
      &@io.Reader,
      &@io.Writer,
      () -> Unit,
    ) = match self.config.transport {
      Tcp => (stream_reader, stream_writer, close_stream)
      WebSocket(path) => {
        let stream : WebSocketStream = {
          reader: stream_reader,
          writer: stream_writer,
          close_transport: close_stream,
        }
        let websocket = @ws.Conn::connect(
          stream,
          websocket_host_header(self.config.host, self.config.port),
          path,
        ) catch {
          error =>
            raise websocket_handshake_error(error, !(self.config.tls is Plain))
        }
        let ws_reader = WebSocketReader::new(websocket)
        let ws_writer : WebSocketWriter = { conn: websocket, }
        (ws_reader, ws_writer, () => websocket.close())
      }
    }
    errdefer close_transport()
    // Encode outside the transport classifier so invalid MQTT configuration can
    // never be mislabeled as a TLS failure.
    let connect_bytes = self.config.connect_bytes()
    let response = mqtt_connect_exchange(
      reader,
      writer,
      connect_bytes,
      self.config.max_packet_size,
      !(self.config.tls is Plain),
      protocol=self.config.protocol,
    )
    let (session_present, negotiated) = match response {
      Incoming311(@codec.ConnackPacket(ack)) => {
        if ack.return_code != @codec.ConnectionAccepted {
          raise ConnectionRefused(@debug.to_string(ack.return_code))
        }
        if self.config.session_policy is CleanSession && ack.session_present {
          raise ProtocolError("CleanSession=true CONNACK set Session Present")
        }
        (
          ack.session_present,
          {
            receive_maximum: self.config.max_inflight,
            max_packet_size: self.config.max_packet_size,
            maximum_qos: AtLeastOnce,
            retain_available: true,
            keep_alive_secs: self.config.keep_alive_secs,
            session_expiry_secs: 0L,
          },
        )
      }
      Incoming5(@mqtt5.Connack(present, reason, properties)) => {
        if reason != 0 {
          raise BrokerRejected(broker_reason(reason, properties))
        }
        if self.config.session_policy is CleanSession && present {
          raise ProtocolError("CleanSession=true CONNACK set Session Present")
        }
        (present, negotiated_settings(self.config, properties))
      }
      _ => raise ProtocolError("expected CONNACK")
    }
    self.generation += 1
    (
      {
        generation: self.generation,
        reader,
        writer,
        close_transport,
        outgoing: @aqueue.Queue(
          kind=@aqueue.Blocking(self.config.send_capacity),
        ),
        control_queue: @aqueue.Queue(
          kind=@aqueue.Blocking(self.config.control_capacity),
        ),
        business_depth: 0,
        control_depth: 0,
        pending: {},
        deliveries: {},
        reserved_deliveries: self.deliveries.reserved,
        delivery_slots: 0,
        qos1_outstanding: 0,
        receive_maximum: negotiated.receive_maximum,
        parked_business: None,
        outgoing_limit: negotiated.max_packet_size,
        delivery_changed: @cond_var.Cond(),
        next_id: 1,
        alive: true,
        last_write: @async.now(),
        ping_sent: None,
        early_messages: [],
        failure_reason: None,
      },
      session_present,
      negotiated,
    )
  })
}

///|

///|
/// A generation ended because its transport was closed from inside the client
/// (a caller-side abort), not because the supervisor was cancelled.
priv suberror SessionLost {
  SessionLost(String)
}

///|
async fn Client::replay_deliveries(self : Client, session : Session) -> Unit {
  let records = self.deliveries.order.copy()
  let negotiated = match self.negotiated {
    Some(value) => value
    None => raise NotConnected
  }
  let incompatibility : String? = if !records.is_empty() &&
    negotiated.maximum_qos == AtMostOnce {
    Some("recovered QoS 1 deliveries exceed negotiated Maximum QoS")
  } else if !negotiated.retain_available && records.any(value => value.retain) {
    Some("recovered retained delivery conflicts with Retain Available=0")
  } else {
    None
  }
  match incompatibility {
    Some(detail) => {
      let cause : Error = DeliveryRejected(detail)
      raise self.block_durable_deliveries(cause)
    }
    None => ()
  }
  for delivery in records {
    if delivery.phase.terminal() {
      continue
    }
    if delivery.attempts > self.config.reconnect_attempts {
      raise DeliveryAttemptsExhausted(
        "delivery \{delivery.delivery_id} exhausted \{delivery.attempts} generation attempts",
      )
    }
    delivery.wire_limit = self.wire_packet_limit()
    match delivery.durable {
      Some(meta) =>
        meta.runtime.transition(async fn() {
          if delivery.message_expiry_at_ms is Some(deadline) &&
            (meta.runtime.now_ms)() >= deadline {
            let error = DeliveryRejected(
              "durable delivery \{delivery.delivery_id} Message Expiry elapsed before replay",
            )
            meta.runtime.outbox.block(
              delivery.delivery_id,
              session_error_reason(error),
            )
            delivery.finish_terminal(session_error_reason(error), error)
            raise error
          }
          match
            meta.runtime.outbox.expire_one(
              delivery.delivery_id,
              (meta.runtime.now_ms)(),
            ) {
            ExpiryStillPending => {
              meta.runtime.outbox.record_attach(delivery.delivery_id)
              delivery.attach(
                session.generation,
                @async.now() + self.config.operation_timeout_ms.to_int64(),
              )
              session.deliveries[delivery.packet_id] = delivery
              session.delivery_slots += 1
            }
            ExpiryRemoved => {
              let error = DeliveryRejected(
                "durable delivery \{delivery.delivery_id} expired before replay and was never possibly written",
              )
              delivery.finish_terminal(session_error_reason(error), error)
              raise error
            }
            ExpiryBlocked => {
              let error = DeliveryRejected(
                "durable delivery \{delivery.delivery_id} expired before replay after a possible write",
              )
              delivery.finish_terminal(session_error_reason(error), error)
              raise error
            }
          }
        }) catch {
          error if @async.is_cancellation_error(error) => raise error
          error => raise client_error_of(error)
        }
      None => {
        delivery.attach(
          session.generation,
          @async.now() + self.config.operation_timeout_ms.to_int64(),
        )
        session.deliveries[delivery.packet_id] = delivery
        session.delivery_slots += 1
      }
    }
    let bytes = delivery.packet(limit=Some(delivery.wire_limit))
    session.enqueue_delivery(delivery, bytes)
  }
}

///|
/// Persist terminal delivery evidence before releasing packet identifiers or
/// payloads. A store failure supersedes the initiating cause because the caller
/// must inspect storage before attempting another network scope.
async fn Client::block_durable_deliveries(
  self : Client,
  cause : Error,
) -> Error {
  guard self.durable is Some(runtime) else { return cause }
  if cause is DurableStorage(_) {
    return cause
  }
  try {
    runtime.transition(async fn() {
      for delivery in self.deliveries.order {
        if !delivery.phase.terminal() && delivery.durable is Some(_) {
          runtime.outbox.block(
            delivery.delivery_id,
            session_error_reason(cause),
          )
        }
      }
    })
    cause
  } catch {
    error => {
      if @async.is_cancellation_error(error) {
        raise error
      }
      match durable_store_error_of(error) {
        Some(store_error) => DurableStorage(store_error)
        None => error
      }
    }
  }
}

///|
/// Record heartbeat/control failures before task-group cancellation reaches the
/// reader and outer cleanup. Cancellation itself remains ordinary teardown.
async fn Session::heartbeat_task(
  self : Session,
  config : Config,
  keep_alive_secs? : Int? = None,
) -> Unit {
  self.heartbeat(config, keep_alive_secs~) catch {
    PingResponseTimeout(reason) => {
      // A caller can end the session while heartbeat sleeps. Its stale timer
      // must not replace the cause that already settled this generation.
      if !self.alive {
        return
      }
      let error = PingResponseTimeout(reason)
      self.abort_with(error)
      raise error
    }
    ProtocolError(reason) => {
      let error = ProtocolError(reason)
      self.abort_with(error)
      raise error
    }
    // The session may be aborted while heartbeat sleeps. In that race,
    // `control` observes the already-completed teardown and returns quietly.
    NotConnected => return
    error => raise error
  }
}

///|
fn server_retry_wait_ms(delay : Int, jitter : Int) -> Int {
  delay + jitter.min(0x7FFFFFFF - delay)
}

///|
fn grow_server_retry_delay(delay : Int, maximum : Int) -> Int {
  delay + (delay / 2).min(maximum - delay)
}

///|
async fn Client::supervise(self : Client) -> Unit {
  let mut attempts = 0
  let mut delay = self.config.reconnect_delay_ms
  // Server-directed retries have a separate lifetime budget and nominal
  // delay. A successful CONNACK deliberately does not reset either value.
  // Jitter still uses the client's seedable stream so test determinism remains
  // covered by Client::set_reconnect_seed.
  let mut server_disconnect_attempts = 0
  let mut server_disconnect_delay = self.config.reconnect_delay_ms
  let mut server_disconnect_dial_pending = false
  let mut ever_ready = false
  defer {
    self.ready = false
    self.stopped = true
    if self.session is Some(session) {
      session.abort("worker ended")
    }
    self.finish_deliveries(self.close_reason())
    self.events.close(error=self.close_reason())
    self.changed.broadcast()
  }
  while !self.stopped {
    // This flag describes exactly this dial. Consuming it before the attempt
    // means a successful CONNACK or a transport failure cannot leak its origin
    // into a later ordinary-network retry.
    let server_disconnect_dial = server_disconnect_dial_pending
    server_disconnect_dial_pending = false
    try {
      let first_connection = !ever_ready
      let (session, session_present, negotiated) = self.dial()
      self.session = Some(session)
      self.negotiated = Some(negotiated)
      defer session.abort("connection ended")
      let locally_known = ever_ready ||
        (match self.durable {
          Some(runtime) => runtime.known_session
          None => false
        })
      if self.config.protocol is Mqtt5 &&
        self.config.session_policy is ResumeSession &&
        first_connection &&
        session_present &&
        !locally_known {
        raise BrokerSessionLost(
          "broker returned Session Present=true without a locally known session",
        )
      }
      if self.config.session_policy is ResumeSession &&
        (
          !first_connection ||
          (
            self.durable is Some(runtime) &&
            (
              runtime.recovered_requires_session_present ||
              runtime.known_session
            )
          )
        ) &&
        !session_present {
        raise BrokerSessionLost(
          "broker returned Session Present=false while recoverable state was active",
        )
      }
      if self.config.protocol is Mqtt5 &&
        self.config.session_policy is ResumeSession {
        match self.durable {
          Some(runtime) =>
            if !runtime.known_session {
              runtime.transition(async fn() {
                runtime.outbox.mark_session_known()
                runtime.known_session = true
              })
            }
          None => ()
        }
      }
      @async.with_task_group(group => {
        group.spawn_bg(() => {
          session.write_loop(
            self.config.operation_timeout_ms,
            self.config.write_timeout_ms,
            self.config.reconnect_attempts,
          )
        })
        group.spawn_bg(() => {
          session.heartbeat_task(
            self.config,
            keep_alive_secs=Some(negotiated.keep_alive_secs),
          )
        })
        group.spawn_bg(() => session.delivery_watchdog(self.config))
        // The read loop drives the connection lifetime. `spawn` instead of
        // `spawn_bg` matters: a failing child cannot wake a group body that is
        // parked on a condition variable, but `task.wait()` propagates the
        // transport error directly, so the supervisor reconnects deterministically.
        //
        // Closing the transport under a blocked read surfaces as a cancellation
        // error. Re-raising it would cancel the supervisor too, because the group
        // body is the coroutine waiting here. The session is already dead at that
        // point, so the cancellation is swallowed and the generation ends
        // normally; the supervisor decides whether to reconnect.
        let reader = group.spawn(() => self.read_loop(session))
        if self.config.session_policy is CleanSession {
          let restore = self.subscriptions
            .iter()
            .map(pair => { topic: pair.0, qos: pair.1, })
            .to_array()
          for subscription in restore {
            let result = self.subscribe_on(session, [subscription])
            if result.any(value => value.result is Rejected) {
              raise ProtocolError("subscription restore rejected")
            }
          }
        }
        if self.config.session_policy is ResumeSession &&
          (
            !first_connection ||
            (self.durable is Some(_) && !self.deliveries.active.is_empty())
          ) {
          self.replay_deliveries(session)
        }
        self.ready = true
        ever_ready = true
        attempts = 0
        delay = self.config.reconnect_delay_ms
        if !first_connection {
          self.reconnects += 1
        }
        self.emit(Connected(session.generation))
        for message in session.early_messages {
          self.emit(MessageReceived(message))
        }
        session.early_messages.clear()
        self.changed.broadcast()
        // Blocks until the transport fails, the peer closes, a heartbeat times
        // out, or the caller aborts the session. Returning here ends the
        // generation and cancels the write loop and heartbeat with the group.
        // The read loop returns normally when the session is shut down from
        // inside the client (caller-side abort or timeout). Any other ending
        // raises the transport error. Either way the generation ends here, and
        // the cancellation from closing the transport is never allowed to mark
        // this supervisor coroutine as cancelled.
        let ended : Result[Unit, Error] = try {
          reader.wait()
          Ok(())
        } catch {
          error => Err(error)
        }
        match ended {
          Ok(_) => raise SessionLost("connection closed while reading")
          Err(error) => raise error
        }
      })
      // A control packet that could not be queued, or a write that exceeded its
      // timeout, leaves the protocol state unsound. The session is aborted so
      // every pending request settles as NotSent or OutcomeUnknown before the
      // error reaches the caller.
      session.abort("session ended")
    } catch {
      error => {
        // The task-group body can observe a pending request's OutcomeUnknown
        // after another loop already recorded the error that caused it. Preserve
        // every such concrete first cause. `SessionLost` is synthetic: it only
        // wins when both the recorded and observed errors are SessionLost, so a
        // generic cleanup cannot mask a direct heartbeat or task-group error.
        let observed : Error = match self.session {
          Some(session) =>
            match session.failure_reason {
              Some(recorded) =>
                match recorded {
                  SessionLost(_) =>
                    match error {
                      SessionLost(_) => recorded
                      _ => error
                    }
                  _ => recorded
                }
              None => error
            }
          None => error
        }
        // Storage methods expose precise DurableStoreError values. Normalize
        // them before terminal-policy matching so replay and ACK DELETE faults
        // fail-stop instead of entering the ordinary transport retry path.
        let cause : Error = match durable_store_error_of(observed) {
          Some(store_error) => DurableStorage(store_error)
          None => observed
        }
        let reason = session_error_reason(cause)
        self.ready = false
        self.session = None
        self.negotiated = None
        self.changed.broadcast()
        if self.stopped {
          self.settle_deliveries(self.close_reason())
          return
        }
        let retry_server_disconnect = match cause {
          ServerDisconnected(broker_reason) =>
            self.config.protocol is Mqtt5 &&
            self.config.server_disconnect_policy is RetryServerBusyOrShutdown &&
            (broker_reason.code == 0x89 || broker_reason.code == 0x8B)
          _ => false
        }
        // Backpressure, a saturated control queue and connect-phase protocol
        // errors are terminal: retrying would hide a defect from the caller.
        match cause {
          BrokerSessionLost(_)
          | DeliveryAttemptsExhausted(_) => {
            // Recoverable handles must remain observable long enough for the
            // application callback to reconcile their terminal state. End the
            // worker normally after recording the typed stop reason instead of
            // letting task-group failure cancel that callback.
            let terminal = self.block_durable_deliveries(cause)
            self.record_stop(terminal)
            self.settle_deliveries(terminal)
            return
          }
          Backpressure(_) | ProtocolError(_) => {
            let terminal = self.block_durable_deliveries(cause)
            self.record_stop(terminal)
            self.settle_deliveries(terminal)
            raise terminal
          }
          DeliveryRejected(_) | DurableStorage(_) | NotConnected => {
            // Expiry has already removed/quarantined its own row. Storage and
            // setup failures retain other pending rows for explicit reopen.
            self.record_stop(cause)
            self.settle_deliveries(cause)
            raise cause
          }
          _ => ()
        }
        // Once the opt-in server-disconnect path has begun, a negative CONNACK
        // cannot continue that reason-directed retry cycle. Preserve the typed
        // broker rejection. Outside this path, the ordinary reconnect policy
        // retains its existing behavior.
        if cause is BrokerRejected(_) && server_disconnect_dial {
          let terminal = self.block_durable_deliveries(cause)
          self.record_stop(terminal)
          self.settle_deliveries(terminal)
          return
        }
        if cause is ServerDisconnected(_) {
          if !retry_server_disconnect ||
            server_disconnect_attempts >= self.config.reconnect_attempts {
            let terminal = self.block_durable_deliveries(cause)
            self.record_stop(terminal)
            self.settle_deliveries(terminal)
            return
          }
          self.emit(Disconnected(self.generation, reason))
          server_disconnect_attempts += 1
          let jitter_ms = self.backoff.next() % server_disconnect_delay
          @async.sleep(
            server_retry_wait_ms(server_disconnect_delay, jitter_ms),
          )
          server_disconnect_delay = grow_server_retry_delay(
            server_disconnect_delay,
            self.config.max_reconnect_delay_ms,
          )
          server_disconnect_dial_pending = true
          continue
        }
        if !ever_ready {
          // A scope that never connected cannot infer a protocol-side terminal
          // outcome. Preserve durable rows pending for a later explicit open.
          self.record_stop(cause)
          self.settle_deliveries(cause)
          raise cause
        }
        self.emit(Disconnected(self.generation, reason))
        attempts += 1
        if attempts > self.config.reconnect_attempts {
          let exhausted = ReconnectExhausted(reason)
          let terminal = self.block_durable_deliveries(exhausted)
          self.record_stop(terminal)
          self.settle_deliveries(terminal)
          raise terminal
        }
        // Bounded exponential backoff plus per-client jitter. Jitter comes from
        // this client's own xorshift stream, so two clients that lose the same
        // broker do not retry in lockstep. `jitter_ms` stays below one nominal
        // delay, keeping the wait in [delay, 2 * delay).
        let jitter_ms = self.backoff.next() % delay
        @async.sleep(delay + jitter_ms)
        delay = (delay + delay / 2).min(self.config.max_reconnect_delay_ms)
      }
    }
  }
}

///|
async fn Client::read_loop(self : Client, session : Session) -> Unit {
  while session.alive {
    // TCP uses cancellable 100 ms slices around each bounded socket operation.
    // WebSocket parsing blocks until data or session close because a timeout
    // after a partial frame header would discard parser state.
    let incoming = try {
      match self.config.transport {
        Tcp =>
          read_session_packet(
            session,
            self.config.protocol,
            self.config.max_packet_size,
          )
        WebSocket(_) =>
          if session.alive {
            // WebSocket frame parsing performs multi-byte reads. Do not wrap it
            // in the TCP path's recurring 100 ms cancellation slices: a timeout
            // after a partial frame header would discard parser state. Session
            // abort closes the underlying stream and wakes this blocking read.
            Some(
              read_wire_packet(
                session.reader,
                self.config.protocol,
                self.config.max_packet_size,
              ),
            )
          } else {
            None
          }
      }
    } catch {
      error => {
        // Preserve the actual framing/I/O cause for pending requests. Never
        // turn task-group cancellation into a new session failure.
        if @async.is_cancellation_error(error) {
          raise error
        }
        session.abort_with(error)
        return
      }
    }
    if incoming is None {
      return
    }
    let packet : IncomingPacket = incoming.unwrap()
    let handled : Result[Unit, Error] = try {
      match normalize_packet(packet) {
        RuntimePublish(packet, properties) => {
          let qos = match packet.qos {
            @codec.QoS0 => AtMostOnce
            @codec.QoS1 => AtLeastOnce
            @codec.QoS2 => {
              session.abort_with(ProtocolError("QoS 2 is not supported"))
              return
            }
          }
          let message : Message = {
            topic: packet.topic,
            payload: packet.payload,
            qos,
            retain: packet.retain,
            dup: packet.dup,
            generation: session.generation,
            properties,
          }
          if self.ready {
            self.emit(MessageReceived(message))
          } else {
            if session.early_messages.length() >= self.config.receive_capacity {
              session.abort_with(
                Backpressure(
                  "messages arrived faster than subscription restoration",
                ),
              )
              return
            }
            session.early_messages.push(message)
          }
          if packet.message_id is Some(id) && qos == AtLeastOnce {
            session.control_wire(
              @codec.PubackPacket({ message_id: id, }),
              self.config,
            ) catch {
              error => {
                session.abort_with(error)
                return
              }
            }
          }
        }
        RuntimePuback(message_id, reason) =>
          match session.deliveries.get(message_id) {
            Some(delivery) => {
              if delivery.generation != Some(session.generation) ||
                !(delivery.phase is AwaitingAck) {
                session.abort_with(
                  ProtocolError(
                    "PUBACK arrived before recoverable PUBLISH write",
                  ),
                )
                return
              }
              match delivery.durable {
                Some(meta) =>
                  meta.runtime.transition(async fn() {
                    // Validation before waiting for the FIFO gate is only a
                    // fast-path protocol check. Expiry or teardown may settle
                    // this owner while ACK handling is queued on the gate.
                    if session.alive &&
                      delivery.generation == Some(session.generation) &&
                      delivery.phase is AwaitingAck &&
                      session.deliveries.contains(message_id) {
                      meta.runtime.outbox.acknowledge(delivery.delivery_id)
                      session.deliveries.remove(message_id)
                      if session.delivery_slots > 0 {
                        session.delivery_slots -= 1
                      }
                      session.return_publish_credit()
                      if reason.code < 128 {
                        self.deliveries.acknowledge(
                          delivery,
                          reason=Some(reason),
                        )
                      } else {
                        self.deliveries.reject(delivery, reason)
                      }
                      session.delivery_changed.broadcast()
                    }
                  }) catch {
                    error if @async.is_cancellation_error(error) => raise error
                    error => raise client_error_of(error)
                  }
                None => {
                  session.deliveries.remove(message_id)
                  if session.delivery_slots > 0 {
                    session.delivery_slots -= 1
                  }
                  session.return_publish_credit()
                  if reason.code < 128 {
                    self.deliveries.acknowledge(delivery, reason=Some(reason))
                  } else {
                    self.deliveries.reject(delivery, reason)
                  }
                  session.delivery_changed.broadcast()
                }
              }
            }
            None =>
              match session.pending.get(message_id) {
                Some(request) if request.kind is Publish1 && request.started => {
                  session.pending.remove(message_id)
                  session.return_publish_credit()
                  if reason.code < 128 {
                    request.finish(Ok(Published(Accepted(reason))))
                  } else {
                    request.finish(Err(BrokerRejected(reason)))
                  }
                }
                _ => {
                  session.abort_with(
                    ProtocolError("unexpected PUBACK identifier"),
                  )
                  return
                }
              }
          }
        RuntimeSuback(message_id, return_codes, diagnostic) =>
          match session.pending.get(message_id) {
            Some(request) =>
              match request.kind {
                Subscribe(topics) if request.started &&
                  topics.length() == return_codes.length() => {
                  let results : Array[SubscriptionAcknowledgement] = []
                  for i in 0..<topics.length() {
                    let result = match return_codes[i] {
                      0 => Granted(AtMostOnce)
                      1 => Granted(AtLeastOnce)
                      code if code >= 128 => Rejected
                      2 => {
                        session.abort_with(
                          ProtocolError("SUBACK granted unsupported QoS"),
                        )
                        return
                      }
                      _ => {
                        session.abort_with(
                          ProtocolError("invalid SUBACK reason"),
                        )
                        return
                      }
                    }
                    match result {
                      Granted(qos) =>
                        if qos == AtLeastOnce && topics[i].qos == AtMostOnce {
                          session.abort_with(
                            ProtocolError("SUBACK exceeds requested QoS"),
                          )
                          return
                        }
                      Rejected => ()
                    }
                    results.push({
                      result,
                      reason: {
                        code: return_codes[i],
                        reason_string: diagnostic.reason_string,
                      },
                    })
                  }
                  // Commit desired subscriptions only after the whole ACK validates.
                  for i in 0..<topics.length() {
                    if results[i].result is Granted(_) {
                      self.subscriptions[topics[i].topic] = topics[i].qos
                    }
                  }
                  session.pending.remove(message_id)
                  request.finish(Ok(SubscriptionsDetailed(results)))
                }
                _ => {
                  session.abort_with(
                    ProtocolError("SUBACK does not match pending request"),
                  )
                  return
                }
              }
            None => {
              session.abort_with(ProtocolError("unexpected SUBACK identifier"))
              return
            }
          }
        RuntimeUnsuback(message_id, reasons, diagnostic) =>
          match session.pending.get(message_id) {
            Some(request) =>
              match request.kind {
                Unsubscribe(topics) if request.started => {
                  if self.config.protocol is Mqtt5 &&
                    topics.length() != reasons.length() {
                    session.abort_with(
                      ProtocolError("UNSUBACK reason count mismatch"),
                    )
                    return
                  }
                  let values = if self.config.protocol is Mqtt311 {
                    topics.map(_ => { code: 0, reason_string: None, })
                  } else {
                    reasons.map(code => {
                      code,
                      reason_string: diagnostic.reason_string,
                    })
                  }
                  for i in 0..<topics.length() {
                    if values[i].code < 128 {
                      self.subscriptions.remove(topics[i])
                    }
                  }
                  session.pending.remove(message_id)
                  request.finish(Ok(Unsubscriptions(values)))
                }
                _ => {
                  session.abort_with(
                    ProtocolError("UNSUBACK does not match pending request"),
                  )
                  return
                }
              }
            None => {
              session.abort_with(
                ProtocolError("unexpected UNSUBACK identifier"),
              )
              return
            }
          }
        RuntimePingresp => session.receive_pingresp()
        RuntimeDisconnect(reason) => {
          session.abort_with(ServerDisconnected(reason))
          return
        }
        RuntimeUnexpected => {
          session.abort_with(ProtocolError("unexpected server packet"))
          return
        }
      }
      Ok(())
    } catch {
      error => Err(error)
    }
    match handled {
      Ok(_) => ()
      Err(error) => {
        // Recording the raw error keeps protocol failures terminal while I/O
        // failures remain retryable. Returning avoids propagating cancellation
        // from transport teardown into the supervisor coroutine.
        session.abort_with(error)
        return
      }
    }
    @async.pause()
  }
}

///|
async fn read_session_byte(session : Session) -> Byte? {
  while session.alive {
    let incoming = @async.with_timeout_opt(100, () => {
      session.reader.read_exactly(1)
    })
    match incoming {
      Some(bytes) => return Some(bytes[0])
      None => ()
    }
  }
  None
}

///|
/// Read an available body chunk in one bounded operation. `read_some` returns as
/// soon as any bytes arrive, so a timeout never cancels a loop that has already
/// consumed an earlier part of the requested body.
async fn read_session_chunk(session : Session, max_len : Int) -> Bytes? {
  while session.alive {
    let incoming = @async.with_timeout_opt(100, () => {
      session.reader.read_some(max_len~)
    })
    match incoming {
      Some(Some(bytes)) => return Some(bytes)
      Some(None) => raise @io.ReaderClosed
      None => ()
    }
  }
  None
}

///|
/// Assemble one MQTT frame across any number of 100 ms idle slices. Parser state
/// stays in this coroutine until the frame is complete or the session is aborted.
async fn read_session_packet(
  session : Session,
  protocol : ProtocolVersion,
  limit : Int,
) -> IncomingPacket? {
  let first = match read_session_byte(session) {
    Some(byte) => byte
    None => return None
  }
  let data : Array[Byte] = [first]
  let mut size = 0
  let mut multiplier = 1
  for index in 0..<4 {
    let digit = match read_session_byte(session) {
      Some(byte) => byte
      None => return None
    }
    data.push(digit)
    size += (digit.to_int() & 127) * multiplier
    if size + data.length() > limit {
      raise ProtocolError("incoming packet exceeds limit")
    }
    if digit.to_int() < 128 {
      if index > 0 && digit == 0 {
        raise ProtocolError("noncanonical remaining length")
      }
      break
    }
    if index == 3 {
      raise ProtocolError("malformed remaining length")
    }
    multiplier *= 128
  }
  let mut received = 0
  while received < size {
    match read_session_chunk(session, size - received) {
      Some(chunk) => {
        for byte in chunk {
          data.push(byte)
        }
        received += chunk.length()
      }
      None => return None
    }
  }
  Some(decode_wire(protocol, Bytes::from_array(data), limit))
}
