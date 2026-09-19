///|
struct AuditV071PingWriter {
  mut writes : Int
  mut entered : Bool
  mut released : Bool
  changed : @cond_var.Cond
}

///|
fn AuditV071PingWriter::new() -> AuditV071PingWriter {
  { writes: 0, entered: false, released: false, changed: @cond_var.Cond(), }
}

///|
async fn AuditV071PingWriter::wait_entered(self : AuditV071PingWriter) -> Unit {
  while !self.entered {
    self.changed.wait()
  }
}

///|
fn AuditV071PingWriter::release(self : AuditV071PingWriter) -> Unit {
  self.released = true
  self.changed.broadcast()
}

///|
impl @io.Writer for AuditV071PingWriter with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  self.writes += 1
  self.entered = true
  self.changed.broadcast()
  while !self.released {
    self.changed.wait()
  }
  offset + len
}

///|
fn audit_v071_a2_session(
  writer : &@io.Writer,
  control_capacity? : Int = 2,
) -> (Session, &@io.Writer) {
  let (reader, peer_writer) = @io.pipe()
  ({
    generation: 1,
    reader,
    writer,
    close_transport: () => {
      reader.close()
      peer_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(control_capacity)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    deliveries: {},
    reserved_deliveries: {},
    delivery_slots: 0,
    qos1_outstanding: 0,
    receive_maximum: 65535,
    parked_business: None,
    outgoing_limit: 4096,
    delivery_changed: @cond_var.Cond(),
    next_id: 1,
    alive: true,
    last_write: @async.now() - 1000L,
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }, peer_writer)
}

///|
fn audit_v071_a2_config(
  timeout_ms? : Int = 5000,
  control_capacity? : Int = 2,
) -> Config {
  Config::new(
    "127.0.0.1",
    "audit-v071-a2",
    protocol=Mqtt311,
    keep_alive_secs=1,
    ping_response_timeout_ms=timeout_ms,
    write_timeout_ms=5000,
    control_capacity=control_capacity,
  )
}

///|
async test "A2 repeated ticks keep only one queued ping" {
  let writer = AuditV071PingWriter::new()
  let session = audit_v071_a2_session(writer).0
  let config = audit_v071_a2_config()
  @async.with_task_group(group => {
    let heartbeat = group.spawn(() => session.heartbeat(config))
    @async.sleep(600)
    let depth = session.control_depth
    session.abort("test complete")
    heartbeat.cancel()
    assert_eq(depth, 1)
  })
}

///|
async test "A2 early PINGRESP stays pending until the current write returns" {
  let writer = AuditV071PingWriter::new()
  let (session, peer_writer) = audit_v071_a2_session(writer)
  let config = audit_v071_a2_config()
  let client = new_client(config, DeliveryBook::new(), None)
  session.queue_ping(config)
  guard session.next_outgoing() is Some(item) else {
    abort("queued PINGREQ missing")
  }
  @async.with_task_group(group => {
    let writing = group.spawn(() => session.write_one(item, 5000, 5000, 2))
    writer.wait_entered()
    let reading = group.spawn(() => client.read_loop(session))
    peer_writer.write(encode(@codec.PingrespPacket, 4096))
    while session.ping_sent == Some(ping_writing()) {
      @async.pause()
    }
    assert_eq(session.ping_sent, Some(ping_responded_while_writing()))
    let heartbeat = group.spawn(() => session.heartbeat(config))
    @async.sleep(300)
    let duplicate_depth = session.control_depth
    writer.release()
    assert_true(writing.wait())
    assert_eq(writer.writes, 1)
    let state_after_write = session.ping_sent
    session.abort("test complete")
    heartbeat.cancel()
    reading.cancel()
    assert_eq(duplicate_depth, 0)
    assert_true(state_after_write is None)
  })
}

///|
async test "A2 PINGRESP timeout starts after blocked write completion" {
  let writer = AuditV071PingWriter::new()
  let session = audit_v071_a2_session(writer, control_capacity=8).0
  let config = audit_v071_a2_config(timeout_ms=100, control_capacity=8)
  @async.with_task_group(group => {
    let writing = group.spawn(() =>
      session.write_loop(5000, 5000, 2)
    )
    let release = group.spawn(() => {
      writer.wait_entered()
      // This exceeds the response timeout, but the PINGREQ write is unfinished.
      @async.sleep(200)
      assert_true(session.alive)
      assert_true(
        (session.ping_sent is Some(value) && value < 0L) ||
        session.ping_sent is None,
      )
      writer.release()
      while session.alive &&
            !(session.ping_sent is Some(value) && value >= 0L) {
        @async.pause()
      }
      assert_true(session.alive)
    })
    try {
      session.heartbeat_task(config)
      abort("missing PINGRESP must time out after write completion")
    } catch {
      PingResponseTimeout(_) => ()
      error => abort("wrong heartbeat result: \{error}")
    }
    release.wait()
    assert_false(session.alive)
    writing.wait()
  })
}

///|
async test "A2 abort clears ping state and blocked completion cannot revive it" {
  let writer = AuditV071PingWriter::new()
  let session = audit_v071_a2_session(writer).0
  let config = audit_v071_a2_config()
  session.queue_ping(config)
  guard session.next_outgoing() is Some(item) else {
    abort("queued PINGREQ missing")
  }
  @async.with_task_group(group => {
    let writing = group.spawn(() => session.write_one(item, 5000, 5000, 2))
    writer.wait_entered()
    session.abort("generation ended")
    writer.release()
    assert_false(writing.wait())
    assert_eq(writer.writes, 1)
    assert_true(session.ping_sent is None)
  })
}

///|
async test "A2 pending ping preserves PUBACK and DISCONNECT control slots" {
  let writer = AuditV071PingWriter::new()
  writer.release()
  let session = audit_v071_a2_session(writer).0
  let config = audit_v071_a2_config()
  session.queue_ping(config)
  session.control_wire(@codec.PubackPacket({ message_id: 7, }), config)
  assert_eq(session.control_depth, 2)
  guard session.next_outgoing() is Some(ping) else {
    abort("queued PINGREQ missing")
  }
  assert_true(ping.is_ping)
  assert_eq(session.control_depth, 1)
  @async.with_task_group(group => {
    let disconnect = group.spawn(() => {
      ignore(
        session.request(
          config,
          Disconnect,
          _ => @codec.DisconnectPacket,
          reason => session.abort(reason),
        ),
      )
    })
    while session.control_depth < 2 {
      @async.pause()
    }
    guard session.next_outgoing() is Some(puback) else {
      abort("queued PUBACK missing")
    }
    assert_false(puback.is_ping)
    assert_true(session.write_one(puback, 5000, 5000, 2))
    guard session.next_outgoing() is Some(close) else {
      abort("queued DISCONNECT missing")
    }
    assert_false(session.write_one(close, 5000, 5000, 2))
    disconnect.wait()
  })
  assert_eq(writer.writes, 2)
  session.abort("test complete")
}

///|
async test "A2 queued PINGREQ ignores PINGRESP until a write can start" {
  let writer = AuditV071PingWriter::new()
  writer.release()
  let (session, peer_writer) = audit_v071_a2_session(writer)
  let config = audit_v071_a2_config(timeout_ms=100)
  let client = new_client(config, DeliveryBook::new(), None)
  client.ready = true
  session.queue_ping(config)
  assert_eq(session.ping_sent, Some(ping_queued()))
  @async.with_task_group(group => {
    let reading = group.spawn(() => client.read_loop(session))
    // The first frame is an unsolicited/old response while this generation's
    // PINGREQ is still queued. The following PUBLISH is an ordering fence that
    // proves read_loop processed the preceding PINGRESP before the dequeue.
    peer_writer.write(encode(@codec.PingrespPacket, 4096))
    peer_writer.write(
      encode(
        @codec.PublishPacket({
          topic: "audit/ping-order",
          payload: b"fence",
          qos: @codec.QoS0,
          message_id: None,
          retain: false,
          dup: false,
        }),
        4096,
      ),
    )
    while client.event_depth == 0 {
      @async.pause()
    }
    assert_eq(session.ping_sent, Some(ping_queued()))
    guard session.next_outgoing() is Some(ping) else {
      abort("queued PINGREQ missing")
    }
    assert_true(session.write_one(ping, 5000, 5000, 2))
    assert_true(session.ping_sent is Some(sent) && sent >= 0L)
    try {
      session.heartbeat_task(config)
      abort("missing response after the real PINGREQ write must time out")
    } catch {
      PingResponseTimeout(_) => ()
      error => abort("wrong heartbeat result: \{error}")
    }
    assert_false(session.alive)
    reading.cancel()
  })
}
