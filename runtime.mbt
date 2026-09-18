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
) -> @codec.Packet {
  try {
    writer.write(connect_bytes)
    read_packet(reader, limit)
  } catch {
    error => {
      if !encrypted {
        raise error
      }
      match error {
        @io.ReaderClosed | @tls.ConnectionClosed =>
          raise TlsFailure("TLS transport closed during MQTT connect")
        @tls.TlsError(message) => raise TlsFailure(message)
        @os_error.OSError(_) =>
          raise TlsFailure(@tls.describe_tls_error(error))
        _ => raise error
      }
    }
  }
}

///|
async fn Client::dial(self : Client) -> Session {
  @async.with_timeout(self.config.connect_timeout_ms, () => {
    let tcp = @socket.Tcp::connect_to_host(
      self.config.host[:],
      port=self.config.port,
    )
    errdefer tcp.close()
    let (reader, writer, close_transport) : (
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
              @tls.Tls::client(
                tcp,
                host=self.config.host,
                trust~,
                identity={
                  certificate_chain_file: id.certificate_chain_file,
                  private_key_file: id.private_key_file,
                },
              )
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
    errdefer close_transport()
    // Encode outside the transport classifier so invalid MQTT configuration can
    // never be mislabeled as a TLS failure.
    let connect_bytes = encode(
      self.config.connect_packet(),
      self.config.max_packet_size,
    )
    let response = mqtt_connect_exchange(
      reader,
      writer,
      connect_bytes,
      self.config.max_packet_size,
      !(self.config.tls is Plain),
    )
    match response {
      @codec.ConnackPacket(ack) => {
        if ack.return_code != @codec.ConnectionAccepted {
          raise ConnectionRefused(@debug.to_string(ack.return_code))
        }
        if ack.session_present {
          raise ProtocolError("CleanSession=true CONNACK set Session Present")
        }
      }
      _ => raise ProtocolError("expected CONNACK")
    }
    self.generation += 1
    {
      generation: self.generation,
      reader,
      writer,
      close_transport,
      outgoing: @aqueue.Queue(kind=@aqueue.Blocking(self.config.send_capacity)),
      control_queue: @aqueue.Queue(
        kind=@aqueue.Blocking(self.config.control_capacity),
      ),
      business_depth: 0,
      control_depth: 0,
      pending: {},
      next_id: 1,
      alive: true,
      last_write: @async.now(),
      ping_sent: None,
      early_messages: [],
      failure_reason: None,
    }
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
/// Record heartbeat/control failures before task-group cancellation reaches the
/// reader and outer cleanup. Cancellation itself remains ordinary teardown.
async fn Session::heartbeat_task(self : Session, config : Config) -> Unit {
  self.heartbeat(config) catch {
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
async fn Client::supervise(self : Client) -> Unit {
  let mut attempts = 0
  let mut delay = self.config.reconnect_delay_ms
  let mut ever_ready = false
  defer {
    self.ready = false
    self.stopped = true
    if self.session is Some(session) {
      session.abort("worker ended")
    }
    self.events.close(error=self.close_reason())
    self.changed.broadcast()
  }
  while !self.stopped {
    try {
      let first_connection = !ever_ready
      let session = self.dial()
      self.session = Some(session)
      defer session.abort("connection ended")
      @async.with_task_group(group => {
        group.spawn_bg(() => {
          session.write_loop(
            self.config.operation_timeout_ms,
            self.config.write_timeout_ms,
          )
        })
        group.spawn_bg(() => session.heartbeat_task(self.config))
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
        let restore = self.subscriptions
          .iter()
          .map(pair => { topic: pair.0, qos: pair.1, })
          .to_array()
        for subscription in restore {
          let result = self.subscribe_on(session, [subscription])
          if result.contains(Rejected) {
            raise ProtocolError("subscription restore rejected")
          }
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
        let cause : Error = match self.session {
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
        let reason = session_error_reason(cause)
        self.ready = false
        self.session = None
        self.changed.broadcast()
        if self.stopped {
          return
        }
        // Backpressure, a saturated control queue and connect-phase protocol
        // errors are terminal: retrying would hide a defect from the caller.
        match cause {
          Backpressure(_) | ProtocolError(_) | NotConnected => {
            self.record_stop(cause)
            raise cause
          }
          _ => ()
        }
        if !ever_ready {
          self.record_stop(cause)
          raise cause
        }
        self.emit(Disconnected(self.generation, reason))
        attempts += 1
        if attempts > self.config.reconnect_attempts {
          let exhausted = ReconnectExhausted(reason)
          self.record_stop(exhausted)
          raise exhausted
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
    // Each bounded operation reads exactly one byte. The frame being assembled
    // lives outside those cancellable slices, so an idle gap between the fixed
    // header, Remaining Length, and body cannot discard bytes already consumed.
    let incoming = try {
      read_session_packet(session, self.config.max_packet_size)
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
    let packet : @codec.Packet = incoming.unwrap()
    let handled : Result[Unit, Error] = try {
      match packet {
      @codec.PublishPacket(packet) => {
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
          session.control(
            @codec.PubackPacket({ message_id: id, }),
            self.config.max_packet_size,
          ) catch {
            error => {
              session.abort_with(error)
              return
            }
          }
        }
      }
      @codec.PubackPacket(ack) =>
        match session.pending.get(ack.message_id) {
          Some(request) if request.kind is Publish1 && request.started => {
            session.pending.remove(ack.message_id)
            request.finish(Ok(Done))
          }
          _ => {
            session.abort_with(ProtocolError("unexpected PUBACK identifier"))
            return
          }
        }
      @codec.SubackPacket(ack) =>
        match session.pending.get(ack.message_id) {
          Some(request) =>
            match request.kind {
              Subscribe(topics) if request.started &&
                topics.length() == ack.return_codes.length() => {
                let results : Array[SubscriptionResult] = []
                for i in 0..<topics.length() {
                  let result = match ack.return_codes[i] {
                    @codec.SuccessQoS0 => Granted(AtMostOnce)
                    @codec.SuccessQoS1 => Granted(AtLeastOnce)
                    @codec.Failure => Rejected
                    @codec.SuccessQoS2 => {
                      session.abort_with(
                        ProtocolError("SUBACK granted unsupported QoS"),
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
                  results.push(result)
                }
                // Commit desired subscriptions only after the whole ACK validates.
                for i in 0..<topics.length() {
                  if results[i] is Granted(_) {
                    self.subscriptions[topics[i].topic] = topics[i].qos
                  }
                }
                session.pending.remove(ack.message_id)
                request.finish(Ok(Subscriptions(results)))
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
      @codec.UnsubackPacket(ack) =>
        match session.pending.get(ack.message_id) {
          Some(request) =>
            match request.kind {
              Unsubscribe(topics) if request.started => {
                for topic in topics {
                  self.subscriptions.remove(topic)
                }
                session.pending.remove(ack.message_id)
                request.finish(Ok(Done))
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
      @codec.PingrespPacket => session.ping_sent = None
      _ => {
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
  limit : Int,
) -> @codec.Packet? {
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
  match @codec.decode_packet(data) {
    Ok(packet) => Some(packet)
    Err(reason) => raise ProtocolError(reason)
  }
}
