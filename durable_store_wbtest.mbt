///|
fn store_test_options(
  directory : String,
  name : String,
  max_records? : Int = 8,
  max_payload_bytes? : Int64 = 1048576L,
  max_pages? : Int = 128,
) -> DurableOutboxOptions {
  DurableOutboxOptions::new(
    "\{directory}/\{name}.sqlite3",
    max_records~,
    max_payload_bytes~,
    max_pages~,
  )
}

///|
async fn raw_sql(connection : @sqlite3.Connection, sql : String) -> Unit {
  let statement = connection.prepare_async(sql)
  defer statement.finalize()
  while true {
    match statement.step_async() {
      @sqlite3.Row => continue
      @sqlite3.Done(..) => break
    }
  }
}

///|
async test "durable record reopens exactly and ACK removal commits" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-reopen")
  defer @fs.rmdir(directory, recursive=true)
  let options = store_test_options(directory, "outbox")
  let store = SqliteOutbox::open(options, "broker|1883|tcp|client-a")
  let admitted = store.admit(
    "delivery-a",
    41,
    "devices/一号/set",
    b"\x00\xff\x80exact",
    true,
    5000L,
    1000L,
  )
  assert_eq(admitted.sequence, 1L)
  store.record_attach("delivery-a")
  store.record_attach("delivery-a")
  store.record_may_write("delivery-a")
  store.close()

  let reopened = SqliteOutbox::open(options, "broker|1883|tcp|client-a")
  let records = reopened.inspect()
  assert_eq(records.length(), 1)
  let record = records[0]
  assert_eq(record.delivery_id, "delivery-a")
  assert_eq(record.packet_id, 41)
  assert_eq(record.topic, "devices/一号/set")
  assert_eq(record.payload, b"\x00\xff\x80exact")
  assert_true(record.retain)
  assert_eq(record.expires_at_ms, 5000L)
  assert_eq(record.attempts, 2)
  assert_true(record.ever_started)
  assert_true(record.state is Pending)
  reopened.acknowledge("delivery-a")
  assert_eq(reopened.inspect().length(), 0)
  reopened.close()

  let after_ack = SqliteOutbox::open(options, "broker|1883|tcp|client-a")
  assert_eq(after_ack.inspect().length(), 0)
  after_ack.close()
}

///|
async test "admission rollback preserves original row and both logical bounds" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-bounds")
  defer @fs.rmdir(directory, recursive=true)
  let options = store_test_options(
    directory,
    "outbox",
    max_records=2,
    max_payload_bytes=6L,
  )
  let store = SqliteOutbox::open(options, "bounds")
  ignore(store.admit("first", 1, "t", b"abc", false, 100L, 1L))
  try {
    ignore(store.admit("first", 2, "changed", b"zzz", true, 100L, 1L))
    abort("duplicate delivery ID was accepted")
  } catch {
    DurableStoreLimit(_) => ()
    error => abort("duplicate admission returned the wrong error: \{error}")
  }
  let original = store.inspect()
  assert_eq(original.length(), 1)
  assert_eq(original[0].packet_id, 1)
  assert_eq(original[0].payload, b"abc")
  ignore(store.admit("second", 2, "t", b"def", false, 100L, 1L))
  try {
    ignore(store.admit("third", 3, "t", b"", false, 100L, 1L))
    abort("record bound was not enforced")
  } catch {
    DurableStoreLimit(_) => ()
    _ => abort("record bound returned the wrong error")
  }
  store.acknowledge("second")
  try {
    ignore(store.admit("too-large", 4, "t", b"1234567", false, 100L, 1L))
    abort("payload bound was not enforced")
  } catch {
    DurableStoreLimit(_) => ()
    _ => abort("payload bound returned the wrong error")
  }
  assert_eq(store.inspect().length(), 1)
  store.close()
  let tighter = store_test_options(
    directory,
    "outbox",
    max_records=2,
    max_payload_bytes=2L,
  )
  try {
    let unexpected = SqliteOutbox::open(tighter, "bounds")
    unexpected.close()
    abort("reopen accepted records beyond its configured payload bound")
  } catch {
    DurableStoreLimit(_) => ()
    _ => abort("reopen logical-bound violation returned the wrong error")
  }
}

///|
async test "expiry removes unsent work and quarantines possible writes" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let store = SqliteOutbox::open(
    store_test_options(directory, "outbox"),
    "expiry",
  )
  ignore(store.admit("unsent", 1, "t", b"a", false, 20L, 1L))
  ignore(store.admit("started", 2, "t", b"b", false, 20L, 1L))
  store.record_may_write("started")
  let sweep = store.expire(20L)
  assert_eq(sweep.removed, ["unsent"])
  assert_eq(sweep.quarantined, ["started"])
  let records = store.inspect()
  assert_eq(records.length(), 1)
  assert_eq(records[0].delivery_id, "started")
  assert_true(records[0].state is Blocked)
  assert_true(records[0].reason.contains("expired"))
  try {
    store.discard_never_started("started")
    abort("started record was discarded")
  } catch {
    DurableStoreState(_) => ()
    _ => abort("started discard returned the wrong error")
  }
  ignore(store.admit("discardable", 3, "t", b"c", false, 30L, 1L))
  store.discard_never_started("discardable")
  assert_eq(store.inspect().length(), 1)
  store.close()
}

///|
async test "explicit blocked cause persists across reopen" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-blocked")
  defer @fs.rmdir(directory, recursive=true)
  let options = store_test_options(directory, "outbox")
  let store = SqliteOutbox::open(options, "blocked")
  ignore(store.admit("blocked", 9, "t", b"body", false, 50L, 1L))
  store.record_may_write("blocked")
  store.block("blocked", "protocol rejection")
  store.close()
  let reopened = SqliteOutbox::open(options, "blocked")
  let records = reopened.inspect()
  assert_eq(records.length(), 1)
  assert_true(records[0].state is Blocked)
  assert_eq(records[0].reason, "protocol rejection")
  assert_true(records[0].ever_started)
  reopened.close()
}

///|
async test "identity mismatch and concurrent owner fail closed" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-owner")
  defer @fs.rmdir(directory, recursive=true)
  let options = store_test_options(directory, "outbox")
  let owner = SqliteOutbox::open(options, "identity-a")
  try {
    let unexpected = SqliteOutbox::open(options, "identity-a")
    unexpected.close()
    abort("concurrent SQLite owner was accepted")
  } catch {
    DurableStoreBusy(_) => ()
    _ => abort("concurrent owner returned the wrong error")
  }
  owner.close()
  try {
    let unexpected = SqliteOutbox::open(options, "identity-b")
    unexpected.close()
    abort("identity mismatch was accepted")
  } catch {
    DurableStoreIdentity(_) => ()
    _ => abort("identity mismatch returned the wrong error")
  }
}

///|
async test "corrupt and unversioned databases are preserved and rejected" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-corrupt")
  defer @fs.rmdir(directory, recursive=true)
  let corrupt = store_test_options(directory, "corrupt")
  @fs.write_file(corrupt.path, "not a sqlite database")
  try {
    let unexpected = SqliteOutbox::open(corrupt, "identity")
    unexpected.close()
    abort("corrupt database was opened")
  } catch {
    DurableStoreCorrupt(_) => ()
    _ => abort("corrupt database returned the wrong error")
  }
  assert_eq(@fs.read_file(corrupt.path).text(), "not a sqlite database")

  let unknown = store_test_options(directory, "unknown-version")
  let unknown_raw = @sqlite3.Connection::open_async(unknown.path)
  raw_sql(unknown_raw, "PRAGMA user_version = 99")
  unknown_raw.close()
  try {
    let unexpected = SqliteOutbox::open(unknown, "identity")
    unexpected.close()
    abort("unknown durable schema version was opened")
  } catch {
    DurableStoreSchema(_) => ()
    _ => abort("unknown schema version returned the wrong error")
  }

  let unrelated = store_test_options(directory, "unrelated")
  let raw = @sqlite3.Connection::open_async(unrelated.path)
  raw_sql(raw, "CREATE TABLE application_data(value TEXT)")
  raw.close()
  try {
    let unexpected = SqliteOutbox::open(unrelated, "identity")
    unexpected.close()
    abort("unversioned nonempty database was initialized")
  } catch {
    DurableStoreSchema(_) => ()
    _ => abort("unversioned database returned the wrong error")
  }
  let verify = @sqlite3.Connection::open_async(unrelated.path)
  let statement = verify.prepare_async(
    "SELECT count(*) FROM sqlite_master WHERE name = 'application_data'",
  )
  assert_true(statement.step_async() is @sqlite3.Row)
  let tables : Int64 = statement.column(index=0)
  assert_eq(tables, 1L)
  ignore(statement.step_async())
  statement.finalize()
  verify.close()
}

///|
async test "read-only and main-file-full failures preserve transaction state" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-failures")
  defer @fs.rmdir(directory, recursive=true)
  let read_only = SqliteOutbox::open(
    store_test_options(directory, "readonly"),
    "readonly",
  )
  raw_sql(read_only.connection, "PRAGMA query_only = ON")
  try {
    ignore(read_only.admit("blocked", 1, "t", b"x", false, 50L, 1L))
    abort("read-only store accepted admission")
  } catch {
    DurableStoreReadOnly(_) => ()
    _ => abort("read-only admission returned the wrong error")
  }
  assert_eq(read_only.inspect().length(), 0)
  raw_sql(read_only.connection, "PRAGMA query_only = OFF")
  read_only.close()

  let full = SqliteOutbox::open(
    store_test_options(
      directory,
      "full",
      max_payload_bytes=1048576L,
      max_pages=8,
    ),
    "full",
  )
  try {
    ignore(
      full.admit(
        "large",
        1,
        "t",
        Bytes::make(262144, b'x'),
        false,
        50L,
        1L,
      ),
    )
    abort("max_page_count did not stop a large admission")
  } catch {
    DurableStoreFull(_) => ()
    _ => abort("full database returned the wrong error")
  }
  assert_eq(full.inspect().length(), 0)
  full.close()
}

///|
async test "cancellation leaves the connection transactionally usable" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-cancel")
  defer @fs.rmdir(directory, recursive=true)
  let store = SqliteOutbox::open(
    store_test_options(directory, "outbox"),
    "cancel",
  )
  @async.with_task_group(group => {
    let admission = group.spawn(() => {
      ignore(store.admit("cancelled", 1, "t", b"x", false, 50L, 1L))
    })
    admission.cancel()
    try admission.wait() catch { _ => () }
  })
  let after_cancel = store.inspect()
  assert_true(after_cancel.length() == 0 || after_cancel.length() == 1)
  ignore(store.admit("after", 2, "t", b"y", false, 50L, 1L))
  assert_true(store.contains_delivery("after"))
  store.close()
  try {
    ignore(store.inspect())
    abort("closed store remained usable")
  } catch {
    DurableStoreState(_) => ()
    _ => abort("closed store returned the wrong error")
  }
}

///|
async test "concurrent durable admissions serialize complete transactions" {
  let directory = @fs.tmpdir(prefix="mqtt-durable-concurrent")
  defer @fs.rmdir(directory, recursive=true)
  let store = SqliteOutbox::open(store_test_options(directory, "outbox"), "concurrent")
  defer store.close()
  @async.with_task_group(group => {
    let first = group.spawn(() => {
      ignore(store.admit("first", 1, "t", b"a", false, 100L, 1L))
    })
    let second = group.spawn(() => {
      ignore(store.admit("second", 2, "t", b"b", false, 100L, 1L))
    })
    first.wait()
    second.wait()
  })
  assert_eq(store.inspect().length(), 2)
}
