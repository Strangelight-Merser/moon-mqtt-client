///|
struct Client {
  config : Config
  events : @aqueue.Queue[Event]
  changed : @cond_var.Cond
  subscriptions : Map[String, QoS]
  mut session : Session?
  mut ready : Bool
  mut stopped : Bool
  mut generation : Int
  mut worker : @async.Task[Unit]?
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
    session: None,
    ready: false,
    stopped: false,
    generation: 0,
    worker: None,
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
pub async fn Client::wait_connected(self : Client) -> Unit {
  while !self.ready || !(self.session is Some(session) && session.alive) {
    if self.stopped {
      raise Closed
    }
    self.changed.wait()
  }
}

///|
pub async fn Client::next_event(self : Client) -> Event {
  self.events.get()
}

///|
fn Client::emit(self : Client, event : Event) -> Unit raise {
  if !self.events.try_put(event) {
    raise Backpressure(
      "event queue full; client terminates instead of dropping messages",
    )
  }
}

///|
fn Client::active(self : Client) -> Session raise ClientError {
  if self.stopped {
    raise Closed
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
  }
  self.pending[id] = request
  defer (if request.result is None { self.abort("request cancelled") })
  if !(kind is Disconnect) &&
    !self.outgoing.try_put({ bytes, pending: Some(request), }) {
    self.pending.remove(id)
    request.finish(Err(Backpressure("send queue full")))
    raise Backpressure("send queue full")
  }
  @async.with_timeout(config.ack_timeout_ms, () => {
    if kind is Disconnect {
      self.outgoing.put({ bytes, pending: Some(request), })
    }
    while request.result is None {
      request.changed.wait()
    }
  }) catch {
    error => {
      self.abort(error.to_string())
      if @async.is_cancellation_error(error) {
        raise error
      }
    }
  }
  match request.result {
    Some(Ok(reply)) => reply
    Some(Err(error)) => raise error
    None => raise Closed
  }
}

///|
pub async fn Client::publish(
  self : Client,
  topic : String,
  payload : Bytes,
  qos? : QoS = AtMostOnce,
  retain? : Bool = false,
) -> Unit {
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
    ),
  )
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
    }) {
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
    }),
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
    if self.worker is Some(worker) {
      worker.cancel()
    }
    self.events.close(error=Closed)
    self.changed.broadcast()
  }
  if self.session is Some(session) && session.alive {
    ignore(
      session.request(self.config, Disconnect, _ => @codec.DisconnectPacket),
    )
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
