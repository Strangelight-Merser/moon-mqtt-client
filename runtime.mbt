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
      let session = self.dial()
      self.session = Some(session)
      defer session.abort("connection ended")
      @async.with_task_group(group => {
        group.spawn_bg(() => session.write_loop(self.config.ack_timeout_ms))
        group.spawn_bg(() => session.heartbeat(self.config))
        group.spawn_bg(() => self.read_loop(session))
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
        self.emit(Connected(session.generation))
        for message in session.early_messages {
          self.emit(MessageReceived(message))
        }
        session.early_messages.clear()
        self.changed.broadcast()
        while session.alive && !self.stopped {
          self.changed.wait()
        }
      }) catch {
        error => {
          session.abort(error.to_string())
          raise error
        }
      }
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
        match error {
          Backpressure(_) => raise error
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
        let jitter = (self.generation * 17 + attempts * 31) % 97
        @async.sleep(delay + jitter)
        delay = (delay + delay / 2).min(self.config.max_reconnect_delay_ms)
      }
    }
  }
}

///|
async fn Client::read_loop(self : Client, session : Session) -> Unit {
  while session.alive {
    match read_packet(session.reader, self.config.max_packet_size) {
      @codec.PublishPacket(packet) => {
        let qos = match packet.qos {
          @codec.QoS0 => AtMostOnce
          @codec.QoS1 => AtLeastOnce
          @codec.QoS2 => raise ProtocolError("QoS 2 is not supported")
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
            raise Backpressure(
              "messages arrived faster than subscription restoration",
            )
          }
          session.early_messages.push(message)
        }
        if packet.message_id is Some(id) && qos == AtLeastOnce {
          session.control(
            @codec.PubackPacket({ message_id: id, }),
            self.config.max_packet_size,
          )
        }
      }
      @codec.PubackPacket(ack) =>
        match session.pending.get(ack.message_id) {
          Some(request) if request.kind is Publish1 && request.started => {
            session.pending.remove(ack.message_id)
            request.finish(Ok(Done))
          }
          _ => raise ProtocolError("unexpected PUBACK identifier")
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
                    @codec.SuccessQoS2 =>
                      raise ProtocolError("SUBACK granted unsupported QoS")
                  }
                  match result {
                    Granted(qos) =>
                      if qos == AtLeastOnce && topics[i].qos == AtMostOnce {
                        raise ProtocolError("SUBACK exceeds requested QoS")
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
              _ => raise ProtocolError("SUBACK does not match pending request")
            }
          None => raise ProtocolError("unexpected SUBACK identifier")
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
              _ =>
                raise ProtocolError("UNSUBACK does not match pending request")
            }
          None => raise ProtocolError("unexpected UNSUBACK identifier")
        }
      @codec.PingrespPacket => session.ping_sent = None
      _ => raise ProtocolError("unexpected server packet")
    }
    @async.pause()
  }
}
