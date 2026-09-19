///|
struct DurableRuntimeSpyWriter {
  mut writes : Int
}

///|
impl @io.Writer for DurableRuntimeSpyWriter with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  self.writes += 1
  offset + len
}

///|
async fn wait_until_gate_is_held(gate : @semaphore.Semaphore) -> Unit {
  for _ in 0..<1000 {
    if !gate.try_acquire() {
      return
    }
    gate.release()
    @async.pause()
  }
  abort("durable transition did not acquire its FIFO gate")
}

///|
fn durable_runtime_config(max_inflight? : Int = 4) -> Config {
  Config::new(
    "127.0.0.1",
    "durable-runtime-test",
    session_policy=ResumeSession,
    operation_timeout_ms=1000,
    write_timeout_ms=1000,
    reconnect_attempts=2,
    max_inflight~,
    send_capacity=2,
  )
}

///|
fn durable_runtime_session(
  config : Config,
  deliveries : DeliveryBook,
  writer : &@io.Writer,
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
    outgoing: @aqueue.Queue(kind=@aqueue.Blocking(config.send_capacity)),
    control_queue: @aqueue.Queue(kind=@aqueue.Blocking(config.control_capacity)),
    business_depth: 0,
    control_depth: 0,
    pending: {},
    deliveries: {},
    reserved_deliveries: deliveries.reserved,
    delivery_slots: 0,
    qos1_outstanding: 0,
    receive_maximum: 65535,
    parked_business: None,
    outgoing_limit: 65536,
    delivery_changed: @cond_var.Cond(),
    next_id: 1,
    alive: true,
    last_write: @async.now(),
    ping_sent: None,
    early_messages: [],
    failure_reason: None,
  }, peer_writer)
}

///|
async fn durable_runtime_client(
  directory : String,
  writer : &@io.Writer,
  now_ms? : () -> Int64 = durable_now_ms,
  max_inflight? : Int = 4,
) -> (Client, Session, SqliteOutbox, &@io.Writer) {
  let config = durable_runtime_config(max_inflight~)
  let options = DurableOutboxOptions::new("\{directory}/outbox.sqlite3")
  let outbox = SqliteOutbox::open(options, durable_identity(config))
  let runtime : DurableRuntime = {
    outbox,
    gate: @semaphore.Semaphore(1),
    recovered_requires_session_present: false,
    known_session: false,
    now_ms,
  }
  let deliveries = DeliveryBook::new()
  let client = new_client(config, deliveries, Some(runtime))
  let (session, peer_writer) = durable_runtime_session(config, deliveries, writer)
  client.session = Some(session)
  client.negotiated = Some(negotiated_settings(config, []))
  client.ready = true
  client.generation = 1
  (client, session, outbox, peer_writer)
}

///|
async test "durable transition commits before write and deletes before handle ACK" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-ack")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, peer_writer) = durable_runtime_client(
    directory,
    writer,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "stable-ack",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  )
  let admitted = outbox.inspect()
  assert_eq(admitted.length(), 1)
  assert_eq(admitted[0].attempts, 1)
  assert_false(admitted[0].ever_started)
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_true(session.write_one(item, 1000, 1000, 2))
  assert_eq(writer.writes, 1)
  assert_true(outbox.inspect()[0].ever_started)
  @async.with_task_group(group => {
    let reader_task = group.spawn(() => client.read_loop(session))
    peer_writer.write(
      encode(
        @codec.PubackPacket({ message_id: handle.status().packet_id, }),
        client.config.max_packet_size,
      ),
    )
    let terminal = handle.wait()
    assert_true(terminal.phase is Acknowledged)
    // The handle wake occurs only after DELETE commits inside the same protected
    // transition, so the empty inspection is observable immediately.
    assert_eq(outbox.inspect().length(), 0)
    session.abort("ACK observed")
    reader_task.cancel()
  })
}

///|
async test "durable pre-write storage failure prevents every socket write" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-prewrite")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(
    directory,
    writer,
  )
  defer {
    session.abort("test complete")
    try raw_sql(outbox.connection, "PRAGMA query_only = OFF") catch { _ => () }
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "no-write",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  )
  raw_sql(outbox.connection, "PRAGMA query_only = ON")
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_false(session.write_one(item, 1000, 1000, 2))
  assert_eq(writer.writes, 0)
  assert_false(handle.status().phase is AwaitingAck)
  let stored = outbox.inspect()
  assert_eq(stored.length(), 1)
  assert_false(stored[0].ever_started)
  guard session.failure_reason is Some(DurableStorage(_)) else {
    abort("pre-write store failure lost its typed durable cause")
  }
}

///|
async test "normal durable scope settlement retains pending storage" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-close")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(
    directory,
    writer,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "survives-close",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  )
  client.settle_deliveries(SessionLost("normal durable scope close"))
  let terminal = handle.status()
  assert_true(terminal.phase is TerminalNotSent(_))
  assert_true(terminal.terminal_cause is Some(Closed))
  let stored = outbox.inspect()
  assert_eq(stored.length(), 1)
  assert_eq(stored[0].delivery_id, "survives-close")
  assert_true(stored[0].state is Pending)
}

///|
async test "failed durable encoding releases its provisional packet reservation" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-encode")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(directory, writer)
  defer {
    session.abort("test complete")
    outbox.close()
  }
  try {
    ignore(client.submit_durable_delivery(
      "invalid-topic",
      "",
      b"payload",
      durable_now_ms() + 10000L,
    ))
    abort("invalid topic was durably admitted")
  } catch {
    ProtocolError(_) => ()
    error => abort("invalid topic returned wrong error: \{error}")
  }
  assert_true(client.deliveries.reserved.is_empty())
  assert_true(client.deliveries.active.is_empty())
  assert_eq(outbox.inspect().length(), 0)
}

///|
async test "admission storage failure fail-stops the client with typed cause" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-admit-fail")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(directory, writer)
  defer {
    try raw_sql(outbox.connection, "PRAGMA query_only = OFF") catch { _ => () }
    session.abort("test complete")
    outbox.close()
  }
  raw_sql(outbox.connection, "PRAGMA query_only = ON")
  try {
    ignore(client.submit_durable_delivery(
      "storage-fails",
      "durable/test",
      b"payload",
      durable_now_ms() + 10000L,
    ))
    abort("read-only durable admission succeeded")
  } catch {
    DurableStorage(DurableStoreReadOnly(_)) => ()
    error => abort("admission lost typed durable cause: \{error}")
  }
  assert_true(client.stopped)
  assert_false(client.ready)
  guard session.failure_reason is Some(DurableStorage(_)) else {
    abort("admission failure did not abort the generation with durable cause")
  }
  assert_eq(writer.writes, 0)
  try {
    ignore(client.submit_durable_delivery(
      "after-failure",
      "durable/test",
      b"payload",
      durable_now_ms() + 10000L,
    ))
    abort("admission after durable failure was accepted")
  } catch {
    DurableStorage(_) => ()
    error => abort("stopped durable client returned wrong cause: \{error}")
  }
}

///|
async test "pre-write commit never writes into a generation that died while waiting" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-dead-generation")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(directory, writer)
  defer {
    session.abort("test complete")
    outbox.close()
  }
  ignore(client.submit_durable_delivery(
    "dead-generation",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  ))
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  outbox.transaction_lock.acquire()
  @async.with_task_group(group => {
    let writing = group.spawn(() => session.write_one(item, 1000, 1000, 2))
    guard client.durable is Some(runtime) else { abort("missing durable runtime") }
    wait_until_gate_is_held(runtime.gate)
    session.abort("generation ended during pre-write persistence")
    outbox.transaction_lock.release()
    assert_false(writing.wait())
  })
  assert_eq(writer.writes, 0)
  assert_true(outbox.inspect()[0].ever_started)
}

///|
async test "ACK queued before teardown commits delete before handle settlement" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-ack-teardown")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, peer_writer) = durable_runtime_client(
    directory,
    writer,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "ack-before-close",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  )
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_true(session.write_one(item, 1000, 1000, 2))
  guard client.durable is Some(runtime) else { abort("missing durable runtime") }
  runtime.gate.acquire()
  @async.with_task_group(group => {
    let reader = group.spawn(() => client.read_loop(session))
    peer_writer.write(
      encode(
        @codec.PubackPacket({ message_id: handle.status().packet_id, }),
        client.config.max_packet_size,
      ),
    )
    // read_loop reaches the held FIFO gate first; teardown queues behind it.
    for _ in 0..<8 {
      @async.pause()
    }
    let teardown = group.spawn(() => {
      client.settle_deliveries(SessionLost("scope ending"))
    })
    for _ in 0..<4 {
      @async.pause()
    }
    runtime.gate.release()
    let terminal = handle.wait()
    assert_true(terminal.phase is Acknowledged)
    teardown.wait()
    assert_eq(outbox.inspect().length(), 0)
    session.abort("test complete")
    reader.cancel()
  })
}

///|
async test "ACK holding the durable gate wins a queued expiry check atomically" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-ack-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let wall = @ref.Ref(1000L)
  let (client, session, outbox, peer_writer) = durable_runtime_client(
    directory,
    writer,
    now_ms=() => wall.val,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "ack-before-expiry",
    "durable/test",
    b"payload",
    1500L,
  )
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_true(session.write_one(item, 1000, 1000, 2))
  guard client.durable is Some(runtime) else { abort("missing durable runtime") }
  // Force ACK processing to own the FIFO gate while its DELETE waits. The
  // expiry task then queues behind that known owner rather than relying on a
  // scheduler race.
  outbox.transaction_lock.acquire()
  @async.with_task_group(group => {
    let reader = group.spawn(() => client.read_loop(session))
    peer_writer.write(
      encode(
        @codec.PubackPacket({ message_id: handle.status().packet_id, }),
        client.config.max_packet_size,
      ),
    )
    wait_until_gate_is_held(runtime.gate)
    wall.val = 2000L
    let expiry = group.spawn(() => session.expire_durable_delivery(handle.record))
    for _ in 0..<4 {
      @async.pause()
    }
    outbox.transaction_lock.release()
    let terminal = handle.wait()
    assert_true(terminal.phase is Acknowledged)
    assert_true(expiry.wait() is None)
    assert_true(session.alive)
    assert_true(session.failure_reason is None)
    assert_eq(outbox.inspect().length(), 0)
    session.abort("test complete")
    reader.cancel()
  })
}

///|
async test "ACK DELETE storage failure fail-stops without a second CONNECT" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-ack-store-fail")
  defer @fs.rmdir(directory, recursive=true)
  let server = @socket.TcpServer(@socket.Addr::parse("127.0.0.1:0"))
  let mut accepts = 0
  let store_is_read_only = @ref.Ref(false)
  let ready_to_ack = @cond_var.Cond()
  let outcome : Result[Unit, Error] = @async.with_task_group(group => {
    group.spawn_bg(no_wait=true, allow_failure=true, () => {
      for ;; {
        let (conn, _) = server.accept()
        accepts += 1
        group.spawn_bg(() => {
          defer conn.close()
          ignore(read_packet(conn, 4096))
          conn.write(encode(
            @codec.ConnackPacket({
              session_present: false,
              return_code: @codec.ConnectionAccepted,
            }),
            4096,
          ))
          guard read_packet(conn, 4096) is @codec.PublishPacket(publish) &&
            publish.message_id is Some(packet_id) else {
            raise Failure::Failure("expected durable QoS 1 PUBLISH")
          }
          while !store_is_read_only.val {
            ready_to_ack.wait()
          }
          conn.write(encode(
            @codec.PubackPacket({ message_id: packet_id, }),
            4096,
          ))
        })
      }
    })
    let result : Result[Unit, Error] = try {
      @async.with_timeout(1000, () => {
        with_durable_client(
          DurableOutboxOptions::new(
            "\{directory}/outbox.sqlite3",
            max_records=4,
          ),
          Config::new(
            "127.0.0.1",
            "durable-ack-storage-failure",
            port=server.addr.port(),
            session_policy=ResumeSession,
            connect_timeout_ms=100,
            operation_timeout_ms=250,
            reconnect_delay_ms=1,
            max_reconnect_delay_ms=1,
            reconnect_attempts=2,
            max_inflight=4,
          ),
          async fn(client, _) {
            let handle = client.submit_durable_delivery(
              "ack-delete-fails",
              "durable/test",
              b"payload",
              durable_now_ms() + 10000L,
            )
            while !(handle.status().phase is AwaitingAck) {
              handle.record.changed.wait()
            }
            guard client.durable is Some(runtime) else {
              abort("missing durable runtime")
            }
            raw_sql(runtime.outbox.connection, "PRAGMA query_only = ON")
            store_is_read_only.val = true
            ready_to_ack.broadcast()
            let terminal = handle.wait()
            guard terminal.terminal_cause is Some(DurableStorage(_)) else {
              abort("ACK DELETE failure lost its typed durable cause")
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
    Err(DurableStorage(DurableStoreReadOnly(_))) => ()
    _ => abort("expected typed ACK storage failure, got \{@debug.to_string(outcome)}")
  }
  assert_eq(accepts, 1)
}

///|
async test "terminal broker-session loss persists blocked cause before release" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-block")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(directory, writer)
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "blocked-session",
    "durable/test",
    b"payload",
    durable_now_ms() + 10000L,
  )
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_true(session.write_one(item, 1000, 1000, 2))
  let cause = BrokerSessionLost("broker discarded the logical session")
  ignore(client.block_durable_deliveries(cause))
  client.settle_deliveries(cause)
  let status = handle.status()
  assert_true(status.phase is TerminalOutcomeUnknown(_))
  assert_true(status.terminal_cause is Some(BrokerSessionLost(_)))
  let stored = outbox.inspect()
  assert_eq(stored.length(), 1)
  assert_true(stored[0].state is Blocked)
  assert_eq(stored[0].reason, "broker discarded the logical session")
}

///|
async test "cancelled admission publishes committed ownership before propagating" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-cancel-admit")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(directory, writer)
  defer {
    session.abort("test complete")
    outbox.close()
  }
  outbox.transaction_lock.acquire()
  @async.with_task_group(group => {
    let admission = group.spawn(() => {
      ignore(client.submit_durable_delivery(
        "cancelled-admission",
        "durable/test",
        b"payload",
        durable_now_ms() + 10000L,
      ))
    })
    guard client.durable is Some(runtime) else { abort("missing durable runtime") }
    wait_until_gate_is_held(runtime.gate)
    admission.cancel()
    outbox.transaction_lock.release()
    try admission.wait() catch {
      error if @async.is_cancellation_error(error) => ()
      error => abort("cancelled admission returned wrong error: \{error}")
    }
  })
  let stored = outbox.inspect()
  assert_eq(stored.length(), 1)
  assert_eq(stored[0].delivery_id, "cancelled-admission")
  assert_eq(stored[0].attempts, 1)
  assert_true(client.deliveries.active.contains("cancelled-admission"))
  assert_true(client.deliveries.reserved.contains(stored[0].packet_id))
  assert_true(session.deliveries.contains(stored[0].packet_id))
  assert_eq(session.business_depth, 0)
}

///|
async test "durable admission reserves the final inflight slot before COMMIT" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-provisional-slot")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let (client, session, outbox, _) = durable_runtime_client(
    directory,
    writer,
    max_inflight=1,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  outbox.transaction_lock.acquire()
  let lock_held = @ref.Ref(true)
  defer {
    if lock_held.val {
      outbox.transaction_lock.release()
    }
  }
  @async.with_task_group(group => {
    let admission = group.spawn(() =>
      client.submit_durable_delivery(
        "holds-final-slot",
        "durable/test",
        b"payload",
        durable_now_ms() + 10000L,
      ))
    guard client.durable is Some(runtime) else { abort("missing durable runtime") }
    wait_until_gate_is_held(runtime.gate)
    assert_eq(session.delivery_slots, 1)
    try {
      client.publish("ordinary/test", b"cannot-steal")
      abort("ordinary request stole durable provisional capacity")
    } catch {
      Backpressure(_) => ()
      error => abort("provisional capacity returned wrong error: \{error}")
    }
    assert_eq(session.pending.length(), 0)
    outbox.transaction_lock.release()
    lock_held.val = false
    let handle = admission.wait()
    assert_eq(handle.id(), "holds-final-slot")
    assert_eq(session.delivery_slots, 1)
    assert_eq(session.deliveries.length(), 1)
  })
}

///|
async test "durable identity rejects encryption and username changes" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-identity")
  defer @fs.rmdir(directory, recursive=true)
  let options = DurableOutboxOptions::new("\{directory}/outbox.sqlite3")
  let plain = durable_runtime_config()
  let store = SqliteOutbox::open(options, durable_identity(plain))
  store.close()
  let encrypted = Config::new(
    plain.host,
    plain.client_id,
    port=plain.port,
    session_policy=ResumeSession,
    tls=SystemRoots,
  )
  try {
    let unexpected = SqliteOutbox::open(options, durable_identity(encrypted))
    unexpected.close()
    abort("plaintext outbox reopened under an encrypted endpoint identity")
  } catch {
    DurableStoreIdentity(_) => ()
    error => abort("scheme mismatch returned wrong error: \{error}")
  }
  let another_username = Config::new(
    plain.host,
    plain.client_id,
    port=plain.port,
    session_policy=ResumeSession,
    username=Some("tenant-b"),
  )
  try {
    let unexpected = SqliteOutbox::open(
      options,
      durable_identity(another_username),
    )
    unexpected.close()
    abort("outbox reopened under another username identity")
  } catch {
    DurableStoreIdentity(_) => ()
    error => abort("username mismatch returned wrong error: \{error}")
  }
}

///|
async test "startup expiry never deletes or rewrites blocked evidence" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-blocked-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let options = DurableOutboxOptions::new("\{directory}/outbox.sqlite3")
  let store = SqliteOutbox::open(options, "blocked-expiry")
  ignore(store.admit("never-started", 1, "t", b"a", false, 20L, 1L))
  store.block("never-started", "operator held unsent record")
  ignore(store.admit("possible-write", 2, "t", b"b", false, 20L, 1L))
  store.record_may_write("possible-write")
  store.block("possible-write", "original protocol cause")
  let sweep = store.expire(20L)
  assert_eq(sweep.removed.length(), 0)
  assert_eq(sweep.quarantined.length(), 0)
  let records = store.inspect()
  assert_eq(records.length(), 2)
  assert_eq(records[0].delivery_id, "never-started")
  assert_eq(records[0].reason, "operator held unsent record")
  assert_eq(records[1].delivery_id, "possible-write")
  assert_eq(records[1].reason, "original protocol cause")
  store.close()
}

///|
async test "rejected durable opens preserve bytes and persistent journal mode" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-rejected-open")
  defer @fs.rmdir(directory, recursive=true)
  let identity = DurableOutboxOptions::new("\{directory}/identity.sqlite3")
  let owner = SqliteOutbox::open(identity, "identity-a")
  owner.close()
  let settings = @sqlite3.Connection::open_async(identity.path)
  assert_eq(
    sqlite_query_text(
      settings,
      "PRAGMA journal_mode = WAL",
      "set rejection sentinel journal",
    ),
    "wal",
  )
  settings.close()
  let identity_before = @fs.read_file(identity.path).binary()
  try {
    let unexpected = SqliteOutbox::open(identity, "identity-b")
    unexpected.close()
    abort("identity mismatch was accepted")
  } catch {
    DurableStoreIdentity(_) => ()
    error => abort("identity mismatch returned wrong error: \{error}")
  }
  assert_eq(@fs.read_file(identity.path).binary(), identity_before)
  let unchanged = @sqlite3.Connection::open_async(identity.path)
  assert_eq(
    sqlite_query_text(
      unchanged,
      "PRAGMA journal_mode",
      "read rejection sentinel journal",
    ),
    "wal",
  )
  unchanged.close()

  let unknown = DurableOutboxOptions::new("\{directory}/unknown.sqlite3")
  let unknown_raw = @sqlite3.Connection::open_async(unknown.path)
  raw_sql(unknown_raw, "PRAGMA user_version = 99")
  unknown_raw.close()
  let unknown_before = @fs.read_file(unknown.path).binary()
  try {
    let unexpected = SqliteOutbox::open(unknown, "identity")
    unexpected.close()
    abort("unknown schema was accepted")
  } catch {
    DurableStoreSchema(_) => ()
    error => abort("unknown schema returned wrong error: \{error}")
  }
  assert_eq(@fs.read_file(unknown.path).binary(), unknown_before)

  let unrelated = DurableOutboxOptions::new("\{directory}/unrelated.sqlite3")
  let unrelated_raw = @sqlite3.Connection::open_async(unrelated.path)
  raw_sql(unrelated_raw, "CREATE TABLE application_data(value TEXT)")
  unrelated_raw.close()
  let unrelated_before = @fs.read_file(unrelated.path).binary()
  try {
    let unexpected = SqliteOutbox::open(unrelated, "identity")
    unexpected.close()
    abort("unrelated database was accepted")
  } catch {
    DurableStoreSchema(_) => ()
    error => abort("unrelated database returned wrong error: \{error}")
  }
  assert_eq(@fs.read_file(unrelated.path).binary(), unrelated_before)
}

///|
async test "durable open revalidates bounds after acquiring exclusive ownership" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-open-race")
  defer @fs.rmdir(directory, recursive=true)
  let path = "\{directory}/outbox.sqlite3"
  let roomy = DurableOutboxOptions::new(path, max_records=2)
  let limited = DurableOutboxOptions::new(path, max_records=1)
  let seed = SqliteOutbox::open(roomy, "race-identity")
  seed.close()
  try {
    let unexpected = SqliteOutbox::open_with_prelock(
      limited,
      "race-identity",
      Some(async fn() {
        // This owner commits after the first connection's read-only preflight
        // but closes before that connection acquires its retained lock.
        let competitor = SqliteOutbox::open(roomy, "race-identity")
        ignore(competitor.admit(
          "raced-a",
          1,
          "t",
          b"a",
          false,
          100L,
          1L,
        ))
        ignore(competitor.admit(
          "raced-b",
          2,
          "t",
          b"b",
          false,
          100L,
          1L,
        ))
        competitor.close()
      }),
    )
    unexpected.close()
    abort("open accepted records committed after its first validation")
  } catch {
    DurableStoreLimit(_) => ()
    error => abort("post-lock revalidation returned wrong error: \{error}")
  }
  let verify = SqliteOutbox::open(roomy, "race-identity")
  assert_eq(verify.inspect().length(), 2)
  verify.close()
}

///|
async test "expiry crossing during possible-write commit never reaches socket" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-cross-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let calls = @ref.Ref(0)
  let clock = () => {
    calls.val += 1
    // Admission and the first pre-write expiry check see a live record. The
    // immediate post-COMMIT check deterministically observes the deadline.
    if calls.val <= 2 { 1000L } else { 2000L }
  }
  let (client, session, outbox, _) = durable_runtime_client(
    directory,
    writer,
    now_ms=clock,
  )
  defer {
    session.abort("test complete")
    outbox.close()
  }
  let handle = client.submit_durable_delivery(
    "cross-expiry",
    "durable/test",
    b"payload",
    1500L,
  )
  guard session.next_outgoing() is Some(item) else {
    abort("durable admission was not queued")
  }
  assert_false(session.write_one(item, 1000, 1000, 2))
  assert_eq(writer.writes, 0)
  assert_true(handle.status().phase is TerminalOutcomeUnknown(_))
  let stored = outbox.inspect()
  assert_eq(stored.length(), 1)
  assert_true(stored[0].ever_started)
  assert_true(stored[0].state is Blocked)
  assert_true(stored[0].reason.contains("expired"))
}

///|
async test "expired detached delivery is removed before replay attachment" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-runtime-replay-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let writer : DurableRuntimeSpyWriter = { writes: 0, }
  let wall = @ref.Ref(1000L)
  let clock = () => wall.val
  let (client, first, outbox, _) = durable_runtime_client(
    directory,
    writer,
    now_ms=clock,
  )
  let handle = client.submit_durable_delivery(
    "expires-before-replay",
    "durable/test",
    b"payload",
    1500L,
  )
  assert_eq(outbox.inspect()[0].attempts, 1)
  first.abort("transport lost before dequeue")
  wall.val = 2000L
  let second = durable_runtime_session(
    client.config,
    client.deliveries,
    writer,
  ).0
  defer {
    second.abort("test complete")
    outbox.close()
  }
  try {
    client.replay_deliveries(second)
    abort("expired detached delivery was attached for replay")
  } catch {
    DeliveryRejected(_) => ()
    error => abort("expired replay returned wrong error: \{error}")
  }
  assert_eq(writer.writes, 0)
  assert_true(handle.status().phase is TerminalNotSent(_))
  assert_eq(handle.status().attempts, 1)
  assert_eq(second.delivery_slots, 0)
  assert_eq(outbox.inspect().length(), 0)
}
