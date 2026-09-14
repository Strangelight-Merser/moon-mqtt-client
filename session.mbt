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
}

///|
priv struct Outgoing {
  bytes : Bytes
  pending : Pending?
}

///|
priv struct Session {
  generation : Int
  reader : &@io.Reader
  writer : &@io.Writer
  close_transport : () -> Unit
  outgoing : @aqueue.Queue[Outgoing]
  pending : Map[Int, Pending]
  mut next_id : Int
  mut alive : Bool
  mut last_write : Int64
  mut ping_sent : Int64?
  early_messages : Array[Message]
  mut failure_reason : String?
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
  if self.alive {
    self.alive = false
    self.failure_reason = Some(reason)
    (self.close_transport)()
    self.outgoing.close(error=Closed, clear=true)
    for _, request in self.pending {
      let error = if request.started {
        OutcomeUnknown(reason)
      } else {
        NotSent(reason)
      }
      request.finish(Err(error))
    }
    self.pending.clear()
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
  if self.pending.length() >= max_inflight {
    raise Backpressure("inflight limit reached")
  }
  for _ in 0..<65535 {
    let id = self.next_id
    self.next_id = if id == 65535 { 1 } else { id + 1 }
    if !self.pending.contains(id) {
      return id
    }
  }
  raise Backpressure("all packet identifiers are occupied")
}

///|
fn Session::control(
  self : Session,
  packet : @codec.Packet,
  limit : Int,
) -> Unit raise {
  if !self.outgoing.try_put({ bytes: encode(packet, limit), pending: None, }) {
    raise Backpressure("outgoing queue cannot accept control packet")
  }
}

///|
async fn Session::write_loop(self : Session, timeout_ms : Int) -> Unit {
  while self.alive {
    let item = self.outgoing.get()
    if item.pending is Some(request) && request.result is Some(_) {
      continue
    }
    if item.pending is Some(request) {
      request.started = true
    }
    @async.with_timeout(timeout_ms, () => self.writer.write(item.bytes))
    self.last_write = @async.now()
    match item.pending {
      Some(request) =>
        match request.kind {
          Publish0 | Disconnect => {
            self.pending.remove(request.id)
            request.finish(Ok(Done))
          }
          _ => ()
        }
      None => ()
    }
    @async.pause()
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
        if now - sent >= config.ack_timeout_ms.to_int64() {
          raise ProtocolError("PINGRESP timeout")
        }
      None =>
        if now - self.last_write >= (interval / 2).to_int64() {
          self.ping_sent = Some(now)
          self.control(@codec.PingreqPacket, config.max_packet_size)
        }
    }
  }
}
