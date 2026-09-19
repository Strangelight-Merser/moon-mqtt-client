///|
/// Fixed bounds for the single SQLite durable-outbox backend.
pub(all) struct DurableOutboxOptions {
  path : String
  max_records : Int
  max_payload_bytes : Int64
  max_pages : Int
}

///|
pub fn DurableOutboxOptions::new(
  path : String,
  max_records? : Int = 32,
  max_payload_bytes? : Int64 = 4194304L,
  max_pages? : Int = 2048,
) -> DurableOutboxOptions {
  {
    path,
    max_records,
    max_payload_bytes,
    max_pages,
  }
}

///|
/// Storage errors retain the distinction needed for safe operator action.
pub(all) suberror DurableStoreError {
  DurableStoreBusy(String)
  DurableStoreFull(String)
  DurableStoreReadOnly(String)
  DurableStoreCorrupt(String)
  DurableStoreIo(String)
  DurableStoreSchema(String)
  DurableStoreIdentity(String)
  DurableStoreLimit(String)
  DurableStoreState(String)
} derive(@debug.Debug)

///|
priv enum PersistedDeliveryState {
  Pending
  Blocked
}

///|
priv struct PersistedDelivery {
  delivery_id : String
  packet_id : Int
  sequence : Int64
  topic : String
  payload : Bytes
  retain : Bool
  expires_at_ms : Int64
  attempts : Int
  ever_started : Bool
  state : PersistedDeliveryState
  reason : String
}

///|
priv struct ExpirySweep {
  removed : Array[String]
  quarantined : Array[String]
}

///|
priv enum DeliveryExpiryResult {
  ExpiryStillPending
  ExpiryRemoved
  ExpiryBlocked
}

///|
/// One concrete SQLite owner. The exclusive lock is retained until `close`.
priv struct SqliteOutbox {
  connection : @sqlite3.Connection
  options : DurableOutboxOptions
  mut open : Bool
  transaction_lock : @semaphore.Semaphore
}

///|
const DURABLE_SCHEMA_VERSION : Int64 = 1L

///|
fn durable_error(
  code : @sqlite3.ErrorCode,
  context : String,
  message : String,
) -> DurableStoreError {
  let detail = "\{context}: \{message}"
  match code {
    @sqlite3.Busy | @sqlite3.Locked => DurableStoreBusy(detail)
    @sqlite3.Full | @sqlite3.TooBig | @sqlite3.NoMem => DurableStoreFull(detail)
    @sqlite3.ReadOnly | @sqlite3.Perm | @sqlite3.Auth =>
      DurableStoreReadOnly(detail)
    @sqlite3.Corrupt | @sqlite3.NotADb | @sqlite3.Format =>
      DurableStoreCorrupt(detail)
    @sqlite3.Schema => DurableStoreSchema(detail)
    @sqlite3.Constraint | @sqlite3.Range => DurableStoreLimit(detail)
    @sqlite3.Misuse => DurableStoreState(detail)
    _ => DurableStoreIo(detail)
  }
}

///|
fn sqlite_error(error : @sqlite3.SqliteError, context : String) -> DurableStoreError {
  match error {
    @sqlite3.SqliteError(code~, msg~, ..) => durable_error(code, context, msg)
  }
}

///|
async fn sqlite_drain(
  connection : @sqlite3.Connection,
  sql : String,
  context : String,
) -> Unit {
  try {
    let statement = connection.prepare_async(sql)
    defer statement.finalize()
    while true {
      match statement.step_async() {
        @sqlite3.Row => continue
        @sqlite3.Done(..) => break
      }
    }
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
}

///|
async fn sqlite_query_int(
  connection : @sqlite3.Connection,
  sql : String,
  context : String,
) -> Int64 {
  try {
    let statement = connection.prepare_async(sql)
    defer statement.finalize()
    guard statement.step_async() is @sqlite3.Row else {
      raise DurableStoreSchema("\{context}: query returned no row")
    }
    let value : Int64 = statement.column(index=0)
    ignore(statement.step_async())
    value
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
}

///|
async fn sqlite_query_text(
  connection : @sqlite3.Connection,
  sql : String,
  context : String,
) -> String {
  try {
    let statement = connection.prepare_async(sql)
    defer statement.finalize()
    guard statement.step_async() is @sqlite3.Row else {
      raise DurableStoreSchema("\{context}: query returned no row")
    }
    let value : String = statement.column(index=0)
    ignore(statement.step_async())
    value
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
}

///|
async fn sqlite_exec_id(
  connection : @sqlite3.Connection,
  sql : String,
  delivery_id : String,
  context : String,
) -> Int64 {
  let statement = try {
    connection.prepare_async(sql)
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
  errdefer (try statement.finalize() catch { _ => () })
  let changed = try {
    statement.bind(index=1, delivery_id)
    match statement.step_async() {
      @sqlite3.Done(changes=Some(changes)) => changes
      _ => connection.changes()
    }
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
  try statement.finalize() catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
  }
  changed
}

///|
async fn sqlite_exec_text_id(
  connection : @sqlite3.Connection,
  sql : String,
  text : String,
  delivery_id : String,
  context : String,
) -> Int64 {
  let statement = try {
    connection.prepare_async(sql)
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
  errdefer (try statement.finalize() catch { _ => () })
  let changed = try {
    statement.bind(index=1, text)
    statement.bind(index=2, delivery_id)
    match statement.step_async() {
      @sqlite3.Done(changes=Some(changes)) => changes
      _ => connection.changes()
    }
  } catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
    error => raise error
  }
  try statement.finalize() catch {
    @sqlite3.SqliteError(..) as error => raise sqlite_error(error, context)
  }
  changed
}

///|
async fn sqlite_begin(connection : @sqlite3.Connection) -> Unit {
  sqlite_drain(connection, "BEGIN IMMEDIATE", "begin durable transaction")
}

///|
async fn sqlite_commit(connection : @sqlite3.Connection) -> Unit {
  sqlite_drain(connection, "COMMIT", "commit durable transaction")
}

///|
async fn sqlite_rollback(connection : @sqlite3.Connection) -> Unit {
  sqlite_drain(connection, "ROLLBACK", "roll back durable transaction")
}

///|
async fn initialize_schema(
  connection : @sqlite3.Connection,
  identity : String,
) -> Unit {
  @async.protect_from_cancel(async fn() {
    sqlite_begin(connection)
    errdefer (try sqlite_rollback(connection) catch { _ => () })
    sqlite_drain(
        connection,
        "CREATE TABLE durable_meta (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), schema_version INTEGER NOT NULL, identity TEXT NOT NULL, next_sequence INTEGER NOT NULL CHECK (next_sequence > 0))",
        "create durable metadata",
      )
    sqlite_drain(
        connection,
        "CREATE TABLE durable_outbox (delivery_id TEXT PRIMARY KEY, packet_id INTEGER NOT NULL UNIQUE CHECK (packet_id BETWEEN 1 AND 65535), sequence INTEGER NOT NULL UNIQUE CHECK (sequence > 0), topic TEXT NOT NULL, payload BLOB NOT NULL, retain INTEGER NOT NULL CHECK (retain IN (0, 1)), expires_at_ms INTEGER NOT NULL, attempts INTEGER NOT NULL CHECK (attempts >= 0), ever_started INTEGER NOT NULL CHECK (ever_started IN (0, 1)), state INTEGER NOT NULL CHECK (state IN (0, 1)), reason TEXT NOT NULL)",
        "create durable outbox",
      )
    let insert = try {
      connection.prepare_async(
          "INSERT INTO durable_meta (singleton, schema_version, identity, next_sequence) VALUES (1, ?, ?, 1)",
        )
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "prepare durable metadata")
      error => raise error
    }
    try {
      insert.bind(index=1, DURABLE_SCHEMA_VERSION)
      insert.bind(index=2, identity)
      ignore(insert.step_async())
      insert.finalize()
    } catch {
      @sqlite3.SqliteError(..) as error => {
        try insert.finalize() catch { _ => () }
        raise sqlite_error(error, "write durable metadata")
      }
      error => {
        try insert.finalize() catch { _ => () }
        raise error
      }
    }
    sqlite_drain(
      connection,
      "PRAGMA user_version = 1",
      "set durable schema version",
    )
    sqlite_commit(connection)
  })
}

///|
async fn validate_schema(
  connection : @sqlite3.Connection,
  identity : String,
) -> Unit {
  let tables = sqlite_query_int(
    connection,
    "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ('durable_meta', 'durable_outbox')",
    "inspect durable tables",
  )
  if tables != 2L {
    raise DurableStoreSchema("durable outbox tables are missing or unknown")
  }
  try {
    let statement = connection.prepare_async(
      "SELECT schema_version, identity FROM durable_meta WHERE singleton = 1",
    )
    defer statement.finalize()
    guard statement.step_async() is @sqlite3.Row else {
      raise DurableStoreSchema("durable metadata row is missing")
    }
    let version : Int64 = statement.column(index=0)
    let stored_identity : String = statement.column(index=1)
    if version != DURABLE_SCHEMA_VERSION {
      raise DurableStoreSchema(
        "unsupported durable schema version \{version}",
      )
    }
    if stored_identity != identity {
      raise DurableStoreIdentity(
        "outbox identity mismatch: stored=\{stored_identity} requested=\{identity}",
      )
    }
    ignore(statement.step_async())
  } catch {
    @sqlite3.SqliteError(..) as error =>
      raise sqlite_error(error, "validate durable metadata")
    error => raise error
  }
}

///|
async fn SqliteOutbox::open(
  options : DurableOutboxOptions,
  identity : String,
) -> SqliteOutbox {
  if options.path == "" || options.max_records <= 0 || options.max_records > 65535 ||
    options.max_payload_bytes <= 0L || options.max_pages <= 0 {
    raise DurableStoreLimit("durable outbox options must be positive")
  }
  if identity == "" {
    raise DurableStoreIdentity("durable outbox identity must not be empty")
  }
  let connection = try {
    @sqlite3.Connection::open_async(options.path)
  } catch {
    @sqlite3.SqliteError(..) as error =>
      raise sqlite_error(error, "open durable outbox")
    error => raise error
  }
  errdefer (try connection.close() catch { _ => () })
  ignore(sqlite_query_int(connection, "PRAGMA busy_timeout = 0", "set busy timeout"))
  if sqlite_query_int(connection, "PRAGMA busy_timeout", "read busy timeout") != 0L {
    raise DurableStoreSchema("SQLite did not retain busy_timeout=0")
  }
  // Every check before the configuration block below is read-only. Rejected
  // unknown schemas, unrelated databases and identity mismatches must retain
  // their original bytes and persistent PRAGMA values.
  let integrity = sqlite_query_text(
    connection,
    "PRAGMA integrity_check",
    "check durable database integrity",
  )
  if integrity != "ok" {
    raise DurableStoreCorrupt("SQLite integrity_check failed: \{integrity}")
  }
  let version = sqlite_query_int(
    connection,
    "PRAGMA user_version",
    "read durable schema version",
  )
  let mut initialize = false
  if version == 0L {
    let tables = sqlite_query_int(
      connection,
      "SELECT count(*) FROM sqlite_master WHERE type = 'table'",
      "inspect unversioned database",
    )
    if tables != 0L {
      raise DurableStoreSchema(
        "refusing to initialize a nonempty unversioned database",
      )
    }
    initialize = true
  } else if version == DURABLE_SCHEMA_VERSION {
    validate_schema(connection, identity)
  } else {
    raise DurableStoreSchema("unsupported durable schema version \{version}")
  }
  if !initialize {
    let record_count = sqlite_query_int(
      connection,
      "SELECT count(*) FROM durable_outbox",
      "validate durable record bound",
    )
    if record_count > options.max_records.to_int64() {
      raise DurableStoreLimit(
        "stored record count exceeds max_records=\{options.max_records}",
      )
    }
    let payload_bytes = sqlite_query_int(
      connection,
      "SELECT coalesce(sum(length(payload)), 0) FROM durable_outbox",
      "validate durable payload bound",
    )
    if payload_bytes > options.max_payload_bytes {
      raise DurableStoreLimit(
        "stored payload bytes exceed max_payload_bytes=\{options.max_payload_bytes}",
      )
    }
  }
  let page_count = sqlite_query_int(
    connection,
    "PRAGMA page_count",
    "read durable page count",
  )
  if page_count > options.max_pages.to_int64() {
    raise DurableStoreLimit(
      "database already exceeds max_pages=\{options.max_pages}",
    )
  }
  let locking = sqlite_query_text(
    connection,
    "PRAGMA locking_mode = EXCLUSIVE",
    "set exclusive locking",
  )
  if locking != "exclusive" {
    raise DurableStoreSchema("SQLite did not retain exclusive locking mode")
  }
  let journal = sqlite_query_text(
    connection,
    "PRAGMA journal_mode = DELETE",
    "set rollback journal mode",
  )
  if journal != "delete" {
    raise DurableStoreSchema("SQLite did not retain DELETE journal mode")
  }
  sqlite_drain(connection, "PRAGMA synchronous = EXTRA", "set EXTRA sync")
  if sqlite_query_int(connection, "PRAGMA synchronous", "read sync policy") != 3L {
    raise DurableStoreSchema("SQLite did not retain synchronous=EXTRA")
  }
  let pages = sqlite_query_int(
    connection,
    "PRAGMA max_page_count = \{options.max_pages}",
    "set main-file page bound",
  )
  if pages != options.max_pages.to_int64() {
    raise DurableStoreLimit(
      "database already exceeds max_pages=\{options.max_pages}",
    )
  }
  if initialize {
    initialize_schema(connection, identity)
  }
  // A completed exclusive transaction acquires the connection's retained
  // exclusive lock. A second owner then fails immediately under busy_timeout=0.
  @async.protect_from_cancel(async fn() {
    sqlite_drain(connection, "BEGIN EXCLUSIVE", "acquire durable ownership")
    sqlite_commit(connection)
  })
  {
    connection,
    options,
    open: true,
    transaction_lock: @semaphore.Semaphore(1),
  }
}

///|
fn SqliteOutbox::ensure_open(self : SqliteOutbox) -> Unit raise DurableStoreError {
  if !self.open {
    raise DurableStoreState("durable outbox is closed")
  }
}

///|
fn SqliteOutbox::close(self : SqliteOutbox) -> Unit raise {
  if self.open {
    if !self.transaction_lock.try_acquire() {
      raise DurableStoreState("cannot close while a storage operation is active")
    }
    defer self.transaction_lock.release()
    try {
      self.connection.close()
      self.open = false
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "close durable outbox")
    }
  }
}

///|
async fn SqliteOutbox::inspect(self : SqliteOutbox) -> Array[PersistedDelivery] {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  try {
    let statement = self.connection.prepare_async(
      "SELECT delivery_id, packet_id, sequence, topic, payload, retain, expires_at_ms, attempts, ever_started, state, reason FROM durable_outbox ORDER BY sequence",
    )
    defer statement.finalize()
    let records : Array[PersistedDelivery] = []
    while true {
      match statement.step_async() {
        @sqlite3.Done(..) => break
        @sqlite3.Row => {
          let packet_id : Int64 = statement.column(index=1)
          let retain : Int64 = statement.column(index=5)
          let attempts : Int64 = statement.column(index=7)
          let ever_started : Int64 = statement.column(index=8)
          let state : Int64 = statement.column(index=9)
          if packet_id < 1L || packet_id > 65535L ||
            attempts < 0L || attempts > 2147483647L ||
            (retain != 0L && retain != 1L) ||
            (ever_started != 0L && ever_started != 1L) ||
            (state != 0L && state != 1L) {
            raise DurableStoreSchema("invalid persisted delivery fields")
          }
          records.push({
            delivery_id: statement.column(index=0),
            packet_id: packet_id.to_int(),
            sequence: statement.column(index=2),
            topic: statement.column(index=3),
            payload: statement.column(index=4),
            retain: retain == 1L,
            expires_at_ms: statement.column(index=6),
            attempts: attempts.to_int(),
            ever_started: ever_started == 1L,
            state: if state == 0L { Pending } else { Blocked },
            reason: statement.column(index=10),
          })
        }
      }
    }
    records
  } catch {
    @sqlite3.SqliteError(..) as error =>
      raise sqlite_error(error, "inspect durable outbox")
    error => raise error
  }
}

///|
async fn SqliteOutbox::admit(
  self : SqliteOutbox,
  delivery_id : String,
  packet_id : Int,
  topic : String,
  payload : Bytes,
  retain : Bool,
  expires_at_ms : Int64,
  now_ms : Int64,
) -> PersistedDelivery {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  if delivery_id == "" || @utf8.encode(delivery_id).length() > 128 {
    raise DurableStoreLimit("delivery_id must contain 1..128 UTF-8 bytes")
  }
  if packet_id < 1 || packet_id > 65535 {
    raise DurableStoreLimit("packet_id must be in 1..65535")
  }
  if expires_at_ms <= now_ms {
    raise DurableStoreLimit("delivery expiry must be in the future")
  }
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    let count = sqlite_query_int(
      self.connection,
      "SELECT count(*) FROM durable_outbox",
      "count durable records",
    )
    if count >= self.options.max_records.to_int64() {
      raise DurableStoreLimit(
        "durable record bound max_records=\{self.options.max_records} reached",
      )
    }
    let payload_bytes = sqlite_query_int(
      self.connection,
      "SELECT coalesce(sum(length(payload)), 0) FROM durable_outbox",
      "count durable payload bytes",
    )
    if payload_bytes > self.options.max_payload_bytes ||
      payload.length().to_int64() > self.options.max_payload_bytes - payload_bytes {
      raise DurableStoreLimit(
        "durable payload bound max_payload_bytes=\{self.options.max_payload_bytes} exceeded",
      )
    }
    let sequence = sqlite_query_int(
      self.connection,
      "SELECT next_sequence FROM durable_meta WHERE singleton = 1",
      "allocate durable insertion order",
    )
    if sequence <= 0L || sequence == 9223372036854775807L {
      raise DurableStoreLimit("durable insertion order exhausted")
    }
    let insert = try {
      self.connection.prepare_async(
        "INSERT INTO durable_outbox (delivery_id, packet_id, sequence, topic, payload, retain, expires_at_ms, attempts, ever_started, state, reason) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '')",
      )
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "prepare durable admission")
      error => raise error
    }
    try {
      insert.bind(index=1, delivery_id)
      insert.bind(index=2, packet_id)
      insert.bind(index=3, sequence)
      insert.bind(index=4, topic)
      insert.bind(index=5, payload)
      insert.bind(index=6, if retain { 1 } else { 0 })
      insert.bind(index=7, expires_at_ms)
      ignore(insert.step_async())
      insert.finalize()
    } catch {
      @sqlite3.SqliteError(..) as error => {
        try insert.finalize() catch { _ => () }
        raise sqlite_error(error, "commit durable admission row")
      }
      error => {
        try insert.finalize() catch { _ => () }
        raise error
      }
    }
    let advance = try {
      self.connection.prepare_async(
        "UPDATE durable_meta SET next_sequence = ? WHERE singleton = 1",
      )
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "prepare durable sequence update")
      error => raise error
    }
    try {
      advance.bind(index=1, sequence + 1L)
      ignore(advance.step_async())
      advance.finalize()
    } catch {
      @sqlite3.SqliteError(..) as error => {
        try advance.finalize() catch { _ => () }
        raise sqlite_error(error, "advance durable insertion order")
      }
      error => {
        try advance.finalize() catch { _ => () }
        raise error
      }
    }
    sqlite_commit(self.connection)
    {
      delivery_id,
      packet_id,
      sequence,
      topic,
      payload,
      retain,
      expires_at_ms,
      attempts: 0,
      ever_started: false,
      state: Pending,
      reason: "",
    }
  })
}

///|
async fn SqliteOutbox::update_one(
  self : SqliteOutbox,
  sql : String,
  delivery_id : String,
  context : String,
) -> Unit {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    let changed = sqlite_exec_id(self.connection, sql, delivery_id, context)
    if changed != 1L {
      raise DurableStoreState("\{context}: delivery not found or blocked")
    }
    sqlite_commit(self.connection)
  })
}

///|
async fn SqliteOutbox::record_attach(
  self : SqliteOutbox,
  delivery_id : String,
) -> Unit {
  self.update_one(
    "UPDATE durable_outbox SET attempts = attempts + 1 WHERE delivery_id = ? AND state = 0 AND attempts < 2147483647",
    delivery_id,
    "record durable generation attachment",
  )
}

///|
async fn SqliteOutbox::record_may_write(
  self : SqliteOutbox,
  delivery_id : String,
) -> Unit {
  self.update_one(
    "UPDATE durable_outbox SET ever_started = 1 WHERE delivery_id = ? AND state = 0",
    delivery_id,
    "record durable possible write",
  )
}

///|
async fn SqliteOutbox::acknowledge(
  self : SqliteOutbox,
  delivery_id : String,
) -> Unit {
  self.update_one(
    "DELETE FROM durable_outbox WHERE delivery_id = ?",
    delivery_id,
    "remove acknowledged durable delivery",
  )
}

///|
async fn SqliteOutbox::block(
  self : SqliteOutbox,
  delivery_id : String,
  reason : String,
) -> Unit {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    let changed = sqlite_exec_text_id(
      self.connection,
      "UPDATE durable_outbox SET state = 1, reason = ? WHERE delivery_id = ?",
      reason,
      delivery_id,
      "quarantine durable delivery",
    )
    if changed != 1L {
      raise DurableStoreState("quarantine durable delivery: delivery not found")
    }
    sqlite_commit(self.connection)
  })
}

///|
async fn SqliteOutbox::expire(
  self : SqliteOutbox,
  now_ms : Int64,
) -> ExpirySweep {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    try {
      let query = try {
        self.connection.prepare_async(
          "SELECT delivery_id, ever_started FROM durable_outbox WHERE state = 0 AND expires_at_ms <= ? ORDER BY sequence",
        )
      } catch {
        @sqlite3.SqliteError(..) as error =>
          raise sqlite_error(error, "prepare durable expiry scan")
        error => raise error
      }
      let removed : Array[String] = []
      let quarantined : Array[String] = []
      try {
        query.bind(index=1, now_ms)
        while true {
          match query.step_async() {
            @sqlite3.Done(..) => break
            @sqlite3.Row => {
              let id : String = query.column(index=0)
              let started : Int64 = query.column(index=1)
              if started == 0L {
                removed.push(id)
              } else {
                quarantined.push(id)
              }
            }
          }
        }
        query.finalize()
      } catch {
        @sqlite3.SqliteError(..) as error => {
          try query.finalize() catch { _ => () }
          raise sqlite_error(error, "scan durable expiry")
        }
        error => {
          try query.finalize() catch { _ => () }
          raise error
        }
      }
      for id in removed {
        ignore(sqlite_exec_id(
          self.connection,
          "DELETE FROM durable_outbox WHERE delivery_id = ? AND state = 0 AND ever_started = 0",
          id,
          "remove expired never-started delivery",
        ))
      }
      for id in quarantined {
        ignore(sqlite_exec_text_id(
          self.connection,
          "UPDATE durable_outbox SET state = 1, reason = ? WHERE delivery_id = ? AND state = 0",
          "expired after a possible write",
          id,
          "quarantine expired possible-write delivery",
        ))
      }
      sqlite_commit(self.connection)
      { removed, quarantined, }
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "apply durable expiry")
      error => raise error
    }
  })
}

///|
/// Apply the absolute expiry transition to one active delivery immediately
/// before a possible write or from its generation watchdog.
async fn SqliteOutbox::expire_one(
  self : SqliteOutbox,
  delivery_id : String,
  now_ms : Int64,
) -> DeliveryExpiryResult {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    let statement = try {
      self.connection.prepare_async(
        "SELECT expires_at_ms, ever_started, state FROM durable_outbox WHERE delivery_id = ?",
      )
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "prepare durable expiry check")
      error => raise error
    }
    let result = try {
      statement.bind(index=1, delivery_id)
      guard statement.step_async() is @sqlite3.Row else {
        raise DurableStoreState(
          "check durable expiry: delivery not found: \{delivery_id}",
        )
      }
      let expires_at_ms : Int64 = statement.column(index=0)
      let ever_started : Int64 = statement.column(index=1)
      let state : Int64 = statement.column(index=2)
      ignore(statement.step_async())
      statement.finalize()
      if state != 0L {
        raise DurableStoreState(
          "check durable expiry: delivery already blocked: \{delivery_id}",
        )
      }
      if expires_at_ms > now_ms {
        ExpiryStillPending
      } else if ever_started == 0L {
        ignore(sqlite_exec_id(
          self.connection,
          "DELETE FROM durable_outbox WHERE delivery_id = ? AND state = 0 AND ever_started = 0",
          delivery_id,
          "remove expired never-started delivery",
        ))
        ExpiryRemoved
      } else {
        ignore(sqlite_exec_text_id(
          self.connection,
          "UPDATE durable_outbox SET state = 1, reason = ? WHERE delivery_id = ? AND state = 0",
          "expired after a possible write",
          delivery_id,
          "quarantine expired possible-write delivery",
        ))
        ExpiryBlocked
      }
    } catch {
      @sqlite3.SqliteError(..) as error => {
        try statement.finalize() catch { _ => () }
        raise sqlite_error(error, "check durable expiry")
      }
      error => {
        try statement.finalize() catch { _ => () }
        raise error
      }
    }
    sqlite_commit(self.connection)
    result
  })
}

///|
async fn SqliteOutbox::contains_delivery(
  self : SqliteOutbox,
  delivery_id : String,
) -> Bool {
  try {
    let statement = self.connection.prepare_async(
      "SELECT count(*) FROM durable_outbox WHERE delivery_id = ?",
    )
    defer statement.finalize()
    statement.bind(index=1, delivery_id)
    guard statement.step_async() is @sqlite3.Row else {
      raise DurableStoreSchema("durable existence query returned no row")
    }
    let count : Int64 = statement.column(index=0)
    ignore(statement.step_async())
    count == 1L
  } catch {
    @sqlite3.SqliteError(..) as error =>
      raise sqlite_error(error, "inspect durable delivery")
    error => raise error
  }
}

///|
async fn SqliteOutbox::discard_never_started(
  self : SqliteOutbox,
  delivery_id : String,
) -> Unit {
  self.transaction_lock.acquire()
  defer self.transaction_lock.release()
  self.ensure_open()
  @async.protect_from_cancel(async fn() {
    sqlite_begin(self.connection)
    errdefer (try sqlite_rollback(self.connection) catch { _ => () })
    try {
      let changed = sqlite_exec_id(
        self.connection,
        "DELETE FROM durable_outbox WHERE delivery_id = ? AND ever_started = 0",
        delivery_id,
        "discard never-started durable delivery",
      )
      if changed != 1L {
        if !self.contains_delivery(delivery_id) {
          raise DurableStoreState("durable delivery not found: \{delivery_id}")
        }
        raise DurableStoreState(
          "started durable delivery cannot be discarded without a logical-session reset",
        )
      }
      sqlite_commit(self.connection)
    } catch {
      @sqlite3.SqliteError(..) as error =>
        raise sqlite_error(error, "discard never-started durable delivery")
      error => raise error
    }
  })
}
