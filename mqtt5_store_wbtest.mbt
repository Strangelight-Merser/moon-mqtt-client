///|
fn mqtt5_store_options(
  directory : String,
  name : String,
  max_records? : Int = 8,
  max_payload_bytes? : Int64 = 1048576L,
) -> DurableOutboxOptions {
  DurableOutboxOptions::new(
    "\{directory}/\{name}.sqlite3",
    max_records~,
    max_payload_bytes~,
    max_pages=128,
  )
}

///|
async fn mqtt5_raw_sql(
  connection : @sqlite3.Connection,
  sql : String,
) -> Unit {
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
async test "MQTT 5 durable metadata reopens exactly in insertion order" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-metadata")
  defer @fs.rmdir(directory, recursive=true)
  let options = mqtt5_store_options(directory, "outbox")
  let metadata = [
    @mqtt5.MessageExpiry(30L),
    @mqtt5.ResponseTopic("app/reply"),
    @mqtt5.CorrelationData(b"\x00\xff\x80binary"),
    @mqtt5.UserProperty("same", "first"),
    @mqtt5.UserProperty("same", "second"),
  ]
  let property_bytes = @mqtt5.encode_publish_properties(metadata, 512)
  let store = SqliteOutbox::open(options, "mqtt5-metadata")
  ignore(store.admit(
    "first",
    41,
    "commands/一号",
    b"payload-a",
    true,
    90000L,
    1000L,
    properties=property_bytes,
    message_expiry_at_ms=Some(31000L),
  ))
  ignore(store.admit(
    "second",
    42,
    "commands/two",
    b"payload-b",
    false,
    90000L,
    1000L,
  ))
  store.close()

  let reopened = SqliteOutbox::open(options, "mqtt5-metadata")
  let records = reopened.inspect()
  assert_eq(records.length(), 2)
  assert_eq(records[0].delivery_id, "first")
  assert_eq(records[0].sequence, 1L)
  assert_eq(records[0].properties, property_bytes)
  assert_eq(records[0].message_expiry_at_ms, Some(31000L))
  assert_eq(
    @mqtt5.decode_publish_properties(records[0].properties, 512),
    metadata,
  )
  assert_eq(records[1].delivery_id, "second")
  assert_eq(records[1].sequence, 2L)
  assert_eq(records[1].properties, b"\x00")
  assert_true(records[1].message_expiry_at_ms is None)
  // Existing D1 transitions retain their ordering and operate on schema 2.
  reopened.record_attach("first")
  reopened.record_may_write("first")
  reopened.acknowledge("first")
  let remaining = reopened.inspect()
  assert_eq(remaining.length(), 1)
  assert_eq(remaining[0].delivery_id, "second")
  reopened.close()
}

///|
async test "known MQTT 5 session marker survives an empty outbox reopen" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-known-session")
  defer @fs.rmdir(directory, recursive=true)
  let options = mqtt5_store_options(directory, "outbox")
  let store = SqliteOutbox::open(options, "known-session")
  assert_false(store.has_known_session())
  store.mark_session_known()
  assert_true(store.has_known_session())
  assert_eq(store.inspect().length(), 0)
  store.close()
  let reopened = SqliteOutbox::open(options, "known-session")
  assert_true(reopened.has_known_session())
  assert_eq(reopened.inspect().length(), 0)
  reopened.close()
}

///|
async test "durable payload bound includes properties and rollback is exact" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-property-bound")
  defer @fs.rmdir(directory, recursive=true)
  let properties = @mqtt5.encode_publish_properties(
    [@mqtt5.UserProperty("key", "value")],
    128,
  )
  let exact_bound = properties.length().to_int64() + 2L
  let options = mqtt5_store_options(
    directory,
    "outbox",
    max_records=3,
    max_payload_bytes=exact_bound,
  )
  let store = SqliteOutbox::open(options, "property-bound")
  ignore(store.admit(
    "fills-bound",
    1,
    "t",
    b"ab",
    false,
    100L,
    1L,
    properties=properties,
  ))
  try {
    ignore(store.admit(
      "over-bound",
      2,
      "t",
      b"",
      false,
      100L,
      1L,
      properties=properties,
    ))
    abort("property bytes were omitted from the durable payload bound")
  } catch {
    DurableStoreLimit(_) => ()
    error => abort("property bound returned wrong error: \{error}")
  }
  let retained = store.inspect()
  assert_eq(retained.length(), 1)
  assert_eq(retained[0].delivery_id, "fills-bound")
  assert_eq(retained[0].properties, properties)
  store.acknowledge("fills-bound")
  let after = store.admit("after", 2, "t", b"a", false, 100L, 1L)
  assert_eq(after.sequence, 2L)
  store.close()
}

///|
async test "oversized property input is bounded before metadata decoding" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-property-precheck")
  defer @fs.rmdir(directory, recursive=true)
  let store = SqliteOutbox::open(
    mqtt5_store_options(
      directory,
      "outbox",
      max_payload_bytes=3L,
    ),
    "property-precheck",
  )
  try {
    ignore(store.admit(
      "oversized-malformed",
      1,
      "t",
      b"",
      false,
      100L,
      1L,
      properties=b"\xff\xff\xff\xff",
    ))
    abort("oversized metadata was decoded or admitted")
  } catch {
    DurableStoreLimit(_) => ()
    error => abort("oversized metadata did not fail at the bound: \{error}")
  }
  assert_eq(store.inspect().length(), 0)
  store.close()
}

///|
async test "admission rejects malformed or expiry-inconsistent metadata" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-invalid-admit")
  defer @fs.rmdir(directory, recursive=true)
  let store = SqliteOutbox::open(
    mqtt5_store_options(directory, "outbox"),
    "invalid-admit",
  )
  let expiry = @mqtt5.encode_publish_properties(
    [@mqtt5.MessageExpiry(30L)],
    32,
  )
  for properties in [b"", b"\x00\x00", b"\x03\x21\x00\x01"] {
    try {
      ignore(store.admit(
        "invalid-\{properties.length()}",
        properties.length() + 1,
        "t",
        b"",
        false,
        100L,
        1L,
        properties=properties,
      ))
      abort("malformed property metadata was admitted")
    } catch {
      DurableStoreState(_) => ()
      error => abort("codec error escaped durable admission: \{error}")
    }
  }
  try {
    ignore(store.admit(
      "expiry-without-deadline",
      10,
      "t",
      b"",
      false,
      100L,
      1L,
      properties=expiry,
    ))
    abort("Message Expiry without an absolute deadline was admitted")
  } catch {
    DurableStoreState(_) => ()
    error => abort("expiry presence mismatch returned wrong error: \{error}")
  }
  try {
    ignore(store.admit(
      "deadline-without-expiry",
      11,
      "t",
      b"",
      false,
      100L,
      1L,
      message_expiry_at_ms=Some(30000L),
    ))
    abort("absolute deadline without Message Expiry was admitted")
  } catch {
    DurableStoreState(_) => ()
    error => abort("deadline presence mismatch returned wrong error: \{error}")
  }
  assert_eq(store.inspect().length(), 0)
  store.close()
}

///|
async test "schema 1 outbox is rejected without byte mutation" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-schema1")
  defer @fs.rmdir(directory, recursive=true)
  let options = mqtt5_store_options(directory, "outbox")
  let raw = @sqlite3.Connection::open_async(options.path)
  mqtt5_raw_sql(
    raw,
    "CREATE TABLE durable_meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL, identity TEXT NOT NULL, next_sequence INTEGER NOT NULL CHECK (next_sequence > 0))",
  )
  mqtt5_raw_sql(
    raw,
    "CREATE TABLE durable_outbox (delivery_id TEXT PRIMARY KEY, packet_id INTEGER NOT NULL UNIQUE, sequence INTEGER NOT NULL UNIQUE, topic TEXT NOT NULL, payload BLOB NOT NULL, retain INTEGER NOT NULL, expires_at_ms INTEGER NOT NULL, attempts INTEGER NOT NULL, ever_started INTEGER NOT NULL, state INTEGER NOT NULL, reason TEXT NOT NULL)",
  )
  mqtt5_raw_sql(
    raw,
    "INSERT INTO durable_meta VALUES (1, 1, 'schema-one', 1)",
  )
  mqtt5_raw_sql(raw, "PRAGMA user_version = 1")
  raw.close()
  let before = @fs.read_file(options.path).binary()
  try {
    let unexpected = SqliteOutbox::open(options, "schema-one")
    unexpected.close()
    abort("schema 1 outbox was silently migrated")
  } catch {
    DurableStoreSchema(_) => ()
    error => abort("schema 1 returned wrong error: \{error}")
  }
  assert_eq(@fs.read_file(options.path).binary(), before)
}

///|
async test "persisted malformed MQTT 5 metadata fails closed as schema" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-corrupt-metadata")
  defer @fs.rmdir(directory, recursive=true)
  let options = mqtt5_store_options(directory, "outbox")
  let store = SqliteOutbox::open(options, "corrupt-metadata")
  ignore(store.admit("corrupt", 1, "t", b"body", false, 100L, 1L))
  store.close()
  let raw = @sqlite3.Connection::open_async(options.path)
  let statement = raw.prepare_async(
    "UPDATE durable_outbox SET properties = ? WHERE delivery_id = 'corrupt'",
  )
  statement.bind(index=1, b"\x03\x21\x00\x01")
  ignore(statement.step_async())
  statement.finalize()
  raw.close()
  try {
    let unexpected = SqliteOutbox::open(options, "corrupt-metadata")
    unexpected.close()
    abort("persisted illegal-direction metadata was accepted")
  } catch {
    DurableStoreSchema(_) => ()
    error => abort("codec error escaped durable reopen: \{error}")
  }
}

///|
async test "reopen enforces cheap metadata bounds before decoding blobs" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-reopen-bound-first")
  defer @fs.rmdir(directory, recursive=true)
  let original_options = mqtt5_store_options(directory, "outbox")
  let store = SqliteOutbox::open(original_options, "bound-before-decode")
  ignore(store.admit("oversized", 1, "t", b"", false, 100L, 1L))
  store.close()
  let raw = @sqlite3.Connection::open_async(original_options.path)
  let statement = raw.prepare_async(
    "UPDATE durable_outbox SET properties = ? WHERE delivery_id = 'oversized'",
  )
  statement.bind(index=1, b"\xff\xff\xff\xff")
  ignore(statement.step_async())
  statement.finalize()
  raw.close()
  let bounded_options = mqtt5_store_options(
    directory,
    "outbox",
    max_payload_bytes=3L,
  )
  try {
    let unexpected = SqliteOutbox::open(
      bounded_options,
      "bound-before-decode",
    )
    unexpected.close()
    abort("oversized persisted metadata was accepted")
  } catch {
    DurableStoreLimit(_) => ()
    error => abort("persisted metadata was decoded before its bound: \{error}")
  }
}

///|
async test "persisted expiry deadline type and presence are validated" {
  let directory = @fs.tmpdir(prefix="mqtt5-durable-corrupt-expiry")
  defer @fs.rmdir(directory, recursive=true)
  let options = mqtt5_store_options(directory, "outbox")
  let store = SqliteOutbox::open(options, "corrupt-expiry")
  ignore(store.admit("corrupt", 1, "t", b"body", false, 100L, 1L))
  store.close()
  let raw = @sqlite3.Connection::open_async(options.path)
  mqtt5_raw_sql(
    raw,
    "PRAGMA ignore_check_constraints = ON",
  )
  mqtt5_raw_sql(
    raw,
    "UPDATE durable_outbox SET message_expiry_at_ms = 'not-an-integer' WHERE delivery_id = 'corrupt'",
  )
  raw.close()
  try {
    let unexpected = SqliteOutbox::open(options, "corrupt-expiry")
    unexpected.close()
    abort("persisted noninteger expiry deadline was accepted")
  } catch {
    DurableStoreSchema(_) => ()
    error => abort("corrupt expiry returned wrong error: \{error}")
  }
}
