///|
fn accepted_connack() -> Bytes raise ClientError {
  encode(
    @codec.ConnackPacket({
      session_present: false,
      return_code: @codec.ConnectionAccepted,
    }),
    4096,
  )
}

///|
struct SlowCauseWriter {}

///|
impl @io.Writer for SlowCauseWriter with fn write_once(
  _self,
  _buf,
  offset~,
  len~,
) {
  @async.sleep(50)
  offset + len
}

///|
priv suberror CauseWriterFailure

///|
struct FailingCauseWriter {}

///|
impl @io.Writer for FailingCauseWriter with fn write_once(
  _self,
  _buf,
  offset~,
  len~,
) {
  ignore(offset)
  ignore(len)
  raise CauseWriterFailure
}

///|
fn cause_test_session(writer : &@io.Writer) -> Session {
  let (reader, pipe_writer) = @io.pipe()
  {
    generation: 1,
    reader,
    writer,
    finish_transport: None,
    close_transport: () => {
      reader.close()
      pipe_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    next_id: 1,
    alive: true,
    last_write: 0L,
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
}

///|
fn cause_test_pending() -> Pending {
  {
    id: 1,
    kind: Publish1,
    changed: @cond_var.Cond(),
    started: false,
    result: None,
    queued_at: @async.now(),
  }
}

///|
async test "write timeout remains typed and outcome stays unknown" {
  let writer = SlowCauseWriter::{  }
  let session = cause_test_session(writer)
  let request = cause_test_pending()
  session.pending[request.id] = request
  assert_false(
    session.write_one(
      { bytes: b"publish", pending: Some(request), is_ping: false, },
      100,
      1,
    ),
  )
  match session.failure_reason {
    Some(WriteTimeout(reason)) =>
      assert_eq(reason, "socket write exceeded write_timeout_ms=1")
    _ => abort("write timeout lost its typed cause")
  }
  match request.result {
    Some(Err(OutcomeUnknown(reason))) =>
      assert_eq(reason, "socket write exceeded write_timeout_ms=1")
    _ => abort("a started timed-out write must stay OutcomeUnknown")
  }
}

///|
async test "writer I/O failure retains its raw stored cause" {
  let writer = FailingCauseWriter::{  }
  let session = cause_test_session(writer)
  let request = cause_test_pending()
  session.pending[request.id] = request
  assert_false(
    session.write_one(
      { bytes: b"publish", pending: Some(request), is_ping: false, },
      100,
      50,
    ),
  )
  match session.failure_reason {
    Some(CauseWriterFailure) => ()
    _ => abort("writer I/O failure was flattened into a timeout")
  }
  match request.result {
    Some(Err(OutcomeUnknown(_))) => ()
    _ => abort("a started failed write must stay OutcomeUnknown")
  }
}

///|
fn cause_test_client(config : Config) -> Client {
  {
    config,
    events: @aqueue.Queue(kind=@aqueue.Blocking(config.receive_capacity)),
    changed: @cond_var.Cond(),
    subscriptions: {},
    session: None,
    ready: true,
    stopped: false,
    generation: 1,
    worker: None,
    event_depth: 0,
    reconnects: 0,
    disconnects: 0,
    unknown_outcomes: 0,
    last_disconnect: None,
    backoff: ReconnectBackoff::new(1),
    stop_reason: None,
  }
}

///|
async test "malformed frame gives started QoS1 the actual unknown reason" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\x30\xff\xff\xff\x80")
  })
  let (_, pipe_writer) = @io.pipe()
  let config = Config::new(
    "127.0.0.1",
    "pending-framing-cause-test",
    max_packet_size=268435455,
  )
  let client = cause_test_client(config)
  let session : Session = {
    generation: 1,
    reader,
    writer: pipe_writer,
    finish_transport: None,
    close_transport: () => {
      reader.close()
      pipe_writer.close()
    },
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(2)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    next_id: 2,
    alive: true,
    last_write: 0L,
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }
  let request : Pending = {
    id: 1,
    kind: Publish1,
    changed: @cond_var.Cond(),
    started: true,
    result: None,
    queued_at: @async.now(),
  }
  session.pending[request.id] = request

  client.read_loop(session)

  match session.failure_reason {
    Some(ProtocolError(reason)) =>
      assert_eq(reason, "malformed remaining length")
    _ => abort("read loop did not preserve the framing error")
  }
  match request.result {
    Some(Err(OutcomeUnknown(reason))) =>
      assert_eq(reason, "malformed remaining length")
    _ => abort("started QoS1 did not retain the framing failure reason")
  }
}

///|
async test "heartbeat failure gives pending request its actual reason" {
  let writer = FailingCauseWriter::{  }
  let session = cause_test_session(writer)
  let request = cause_test_pending()
  request.started = true
  session.pending[request.id] = request
  session.ping_sent = Some(@async.now() - 10L)
  let config = Config::new(
    "127.0.0.1",
    "heartbeat-cause-test",
    keep_alive_secs=1,
    ping_response_timeout_ms=1,
  )

  try {
    session.heartbeat_task(config)
    abort("heartbeat timeout must fail")
  } catch {
    PingResponseTimeout(reason) =>
      assert_eq(
        reason,
        "no PINGRESP within 1 ms of the completed PINGREQ write",
      )
    _ => abort("expected PingResponseTimeout")
  }
  match request.result {
    Some(Err(OutcomeUnknown(reason))) =>
      assert_eq(
        reason,
        "no PINGRESP within 1 ms of the completed PINGREQ write",
      )
    _ => abort("heartbeat failure did not settle the pending request")
  }
}

///|
async test "heartbeat preserves the cause recorded while it was sleeping" {
  let session = cause_test_session(FailingCauseWriter::{  })
  session.ping_sent = Some(@async.now() - 10L)
  let config = Config::new(
    "127.0.0.1",
    "heartbeat-abort-race",
    keep_alive_secs=1,
    ping_response_timeout_ms=1,
  )
  let started = @cond_var.Cond()
  let mut heartbeat_started = false
  @async.with_task_group(group => {
    let heartbeat = group.spawn(() => {
      heartbeat_started = true
      started.broadcast()
      session.heartbeat_task(config)
    })
    while !heartbeat_started {
      started.wait()
    }
    session.abort("request cancelled")
    heartbeat.wait()
  })
  match session.failure_reason {
    Some(SessionLost(reason)) => assert_eq(reason, "request cancelled")
    _ => abort("a stale heartbeat timeout replaced the original cause")
  }
}

///|
async test "post-CONNACK protocol failure is terminal and keeps its type" {
  let server = @socket.TcpServer(@socket.Addr::parse("127.0.0.1:0"))
  let mut accepts = 0
  let outcome : Result[Unit, Error] = @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, allow_failure=true, () => {
      for ;; {
        let (conn, _) = server.accept()
        accepts += 1
        let connection_number = accepts
        group.spawn_bg(() => {
          defer conn.close()
          ignore(read_packet(conn, 4096))
          if connection_number == 1 {
            conn.write(accepted_connack())
            match read_packet(conn, 4096) {
              @codec.PublishPacket(_) => ()
              _ => raise Failure::Failure("expected readiness PUBLISH")
            }
            conn.write(
              encode(@codec.PubackPacket({ message_id: 77 }), 4096),
            )
          }
        })
      }
    })
    let result : Result[Unit, Error] = try {
      @async.with_timeout(500, () => {
        with_client(
          Config::new(
            "127.0.0.1",
            "terminal-cause-test",
            port=server.addr.port(),
            connect_timeout_ms=50,
            operation_timeout_ms=100,
            reconnect_delay_ms=1,
            max_reconnect_delay_ms=1,
            reconnect_attempts=1,
          ),
          async fn(client) {
            client.publish("ready", b"")
            for ;; {
              ignore(client.next_event())
            }
          },
        )
      })
      Ok(())
    } catch {
      error => Err(error)
    }
    server.close()
    result
  })
  match outcome {
    Err(ProtocolError(reason)) =>
      assert_eq(reason, "unexpected PUBACK identifier")
    _ => abort("expected typed ProtocolError, got \{@debug.to_string(outcome)}")
  }
  assert_eq(accepts, 1)
}

///|
async test "direct framing error outranks teardown cleanup" {
  let server = @socket.TcpServer(@socket.Addr::parse("127.0.0.1:0"))
  let mut accepts = 0
  let outcome : Result[Unit, Error] = @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, () => {
      let (conn, _) = server.accept()
      accepts += 1
      defer conn.close()
      ignore(read_packet(conn, 4096))
      conn.write(accepted_connack())
      match read_packet(conn, 4096) {
        @codec.PublishPacket(_) => ()
        _ => raise Failure::Failure("expected readiness PUBLISH")
      }
      // Four continuation bytes violate MQTT's Remaining Length encoding.
      conn.write(b"\x30\xff\xff\xff\x80")
    })
    let result : Result[Unit, Error] = try {
      @async.with_timeout(500, () => {
        with_client(
          Config::new(
            "127.0.0.1",
            "direct-cause-test",
            port=server.addr.port(),
            connect_timeout_ms=50,
            max_packet_size=268435455,
            reconnect_delay_ms=1,
            max_reconnect_delay_ms=1,
            reconnect_attempts=1,
          ),
          async fn(client) {
            client.publish("ready", b"")
            for ;; {
              ignore(client.next_event())
            }
          },
        )
      })
      Ok(())
    } catch {
      error => Err(error)
    }
    server.close()
    result
  })
  match outcome {
    Err(ProtocolError(reason)) =>
      assert_eq(reason, "malformed remaining length")
    _ => abort("expected framing ProtocolError, got \{@debug.to_string(outcome)}")
  }
  assert_eq(accepts, 1)
}

///|
async test "invalid SUBACK during restore keeps protocol cause terminal" {
  let server = @socket.TcpServer(@socket.Addr::parse("127.0.0.1:0"))
  let mut accepts = 0
  let outcome : Result[Unit, Error] = @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, allow_failure=true, () => {
      for ;; {
        let (conn, _) = server.accept()
        accepts += 1
        let connection_number = accepts
        group.spawn_bg(() => {
          defer conn.close()
          ignore(read_packet(conn, 4096))
          if connection_number <= 2 {
            conn.write(accepted_connack())
            let request = read_packet(conn, 4096)
            match request {
              @codec.SubscribePacket(subscribe) => {
                let code = if connection_number == 1 {
                  @codec.SuccessQoS0
                } else {
                  @codec.SuccessQoS2
                }
                conn.write(
                  encode(
                    @codec.SubackPacket({
                      message_id: subscribe.message_id,
                      return_codes: [code],
                    }),
                    4096,
                  ),
                )
                if connection_number == 1 {
                  match read_packet(conn, 4096) {
                    @codec.PublishPacket(_) => ()
                    _ => raise Failure::Failure("expected readiness PUBLISH")
                  }
                }
              }
              _ => raise Failure::Failure("expected SUBSCRIBE")
            }
          }
        })
      }
    })
    let result : Result[Unit, Error] = try {
      @async.with_timeout(700, () => {
        with_client(
          Config::new(
            "127.0.0.1",
            "restore-cause-test",
            port=server.addr.port(),
            connect_timeout_ms=50,
            operation_timeout_ms=100,
            reconnect_delay_ms=1,
            max_reconnect_delay_ms=1,
            reconnect_attempts=2,
          ),
          async fn(client) {
            ignore(
              client.subscribe([
                { topic: "restore/cause", qos: AtMostOnce, },
              ]),
            )
            client.publish("ready", b"")
            for ;; {
              ignore(client.next_event())
            }
          },
        )
      })
      Ok(())
    } catch {
      error => Err(error)
    }
    server.close()
    result
  })
  match outcome {
    Err(ProtocolError(reason)) =>
      assert_eq(reason, "SUBACK granted unsupported QoS")
    _ => abort("expected restore ProtocolError, got \{@debug.to_string(outcome)}")
  }
  assert_eq(accepts, 2)
}

///|
async test "post-CONNACK transport loss still reconnects" {
  let server = @socket.TcpServer(@socket.Addr::parse("127.0.0.1:0"))
  let mut accepts = 0
  @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, () => {
      let (first, _) = server.accept()
      accepts += 1
      ignore(read_packet(first, 4096))
      first.write(accepted_connack())
      match read_packet(first, 4096) {
        @codec.PublishPacket(_) => ()
        _ => raise Failure::Failure("expected readiness PUBLISH")
      }
      first.close()

      let (second, _) = server.accept()
      accepts += 1
      defer second.close()
      ignore(read_packet(second, 4096))
      second.write(accepted_connack())
      ignore(read_packet(second, 4096))
    })
    @async.with_timeout(500, () => {
      with_client(
        Config::new(
          "127.0.0.1",
          "retryable-cause-test",
          port=server.addr.port(),
          connect_timeout_ms=50,
          reconnect_delay_ms=1,
          max_reconnect_delay_ms=1,
          reconnect_attempts=2,
        ),
        async fn(client) {
          client.publish("ready", b"")
          let mut saw_disconnect = false
          for ;; {
            match client.next_event() {
              Disconnected(1, _) => saw_disconnect = true
              Connected(2) => break
              _ => ()
            }
          }
          let stats = client.stats()
          assert_true(saw_disconnect)
          assert_eq(stats.generation, 2)
          assert_eq(stats.reconnects, 1)
          assert_eq(stats.disconnects, 1)
        },
      )
    })
    server.close()
  })
  assert_eq(accepts, 2)
}
