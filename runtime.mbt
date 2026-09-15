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
        let tls = @tls.Tls::client(tcp, host=self.config.host, trust~)
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
    writer.write(
      encode(self.config.connect_packet(), self.config.max_packet_size),
    )
    match read_packet(reader, self.config.max_packet_size) {
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
    self.events.close(error=Closed)
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
        group.spawn_bg(() => session.heartbeat(self.config))
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
        let reason = match self.session {
          Some(session) => session.failure_reason.unwrap_or(error.to_string())
          None => error.to_string()
        }
        self.ready = false
        self.session = None
        self.changed.broadcast()
        if self.stopped {
          return
        }
        // Backpressure, a saturated control queue and connect-phase protocol
        // errors are terminal: retrying would hide a defect from the caller.
        match error {
          Backpressure(_) | ProtocolError(_) | NotConnected => raise error
          _ => ()
        }
        if !ever_ready {
          raise error
        }
        self.emit(Disconnected(self.generation, reason))
        attempts += 1
        if attempts > self.config.reconnect_attempts {
          raise ReconnectExhausted(reason)
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
    // Read with a bounded wait instead of one unbounded blocking read. Closing
    // the transport underneath a blocked read surfaces as a cancellation of
    // this coroutine, and a cancelled task inside a task group poisons the
    // supervisor's own coroutine through the group's failure bookkeeping. With
    // a bounded wait the loop instead observes `session.alive` and returns
    // normally, so a caller-side abort stays a normal generation end.
    let incoming = @async.with_timeout_opt(100, () => {
      read_packet(session.reader, self.config.max_packet_size)
    })
    if incoming is None {
      continue
    }
    let packet : @codec.Packet = incoming.unwrap()
    let handled : Result[Unit, Error] = try {
      match packet {
      @codec.PublishPacket(packet) => {
        let qos = match packet.qos {
          @codec.QoS0 => AtMostOnce
          @codec.QoS1 => AtLeastOnce
          @codec.QoS2 => {
            session.abort("QoS 2 is not supported")
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
            session.abort("messages arrived faster than subscription restoration")
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
              session.abort("cannot acknowledge incoming message: " + @debug.to_string(error))
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
            session.abort("unexpected PUBACK identifier")
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
                      session.abort("SUBACK granted unsupported QoS")
                      return
                    }
                  }
                  match result {
                    Granted(qos) =>
                      if qos == AtLeastOnce && topics[i].qos == AtMostOnce {
                        session.abort("SUBACK exceeds requested QoS")
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
                session.abort("SUBACK does not match pending request")
                return
              }
            }
          None => {
            session.abort("unexpected SUBACK identifier")
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
                session.abort("UNSUBACK does not match pending request")
                return
              }
            }
          None => {
            session.abort("unexpected UNSUBACK identifier")
            return
          }
        }
      @codec.PingrespPacket => session.ping_sent = None
      _ => {
        session.abort("unexpected server packet")
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
        // A transport or decode failure ends this generation. Recording it on
        // the session (instead of raising) keeps the supervisor's coroutine
        // clean and lets the supervisor classify and reconnect.
        session.abort(error.to_string())
        return
      }
    }
    @async.pause()
  }
}


