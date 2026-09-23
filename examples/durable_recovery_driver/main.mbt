///|
fn required(name : String) -> String raise {
  @env.get_env_var(name).unwrap_or_else(() => fail("missing \{name}"))
}

///|
async fn log(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
/// Test-driver-only process crash. `_exit` intentionally skips task unwinding,
/// SQLite close and buffered runtime cleanup.
extern "C" fn hard_exit(code : Int) = "_exit"

///|
fn config() -> @mqtt.Config raise {
  @mqtt.Config::new(
    "127.0.0.1",
    @env.get_env_var("D1_CLIENT_ID").unwrap_or("moon-durable-recovery"),
    port=@string.from_str(required("D1_PORT")),
    session_policy=@mqtt.ResumeSession,
    operation_timeout_ms=@string.from_str(
      @env.get_env_var("D1_OPERATION_TIMEOUT_MS").unwrap_or("5000"),
    ),
    write_timeout_ms=1000,
    reconnect_delay_ms=20,
    max_reconnect_delay_ms=50,
    reconnect_attempts=@string.from_str(
      @env.get_env_var("D1_RECONNECT_ATTEMPTS").unwrap_or("3"),
    ),
    max_inflight=4,
    send_capacity=4,
  )
}

///|
fn options() -> @mqtt.DurableOutboxOptions raise {
  @mqtt.DurableOutboxOptions::new(
    required("D1_OUTBOX"),
    max_records=4,
    max_payload_bytes=65536L,
    max_pages=128,
  )
}

///|
async fn print_inspection(records : Array[@mqtt.DurableDeliveryInfo]) -> Unit {
  log("records=\{records.length()}")
  for record in records {
    let state = match record.state {
      @mqtt.DurablePending => "pending"
      @mqtt.DurableBlocked(reason) => "blocked:\{reason}"
    }
    log(
      "record id=\{record.delivery_id} packet=\{record.packet_id} sequence=\{record.sequence} attempts=\{record.attempts} started=\{record.ever_started} state=\{state} payload=\{@utf8.decode(record.payload)}",
    )
  }
}

///|
async fn main {
  let mode = @env.get_env_var("D1_MODE").unwrap_or("run")
  let config = config()
  let options = options()
  if mode == "inspect" {
    print_inspection(@mqtt.inspect_durable_outbox(options, config))
    return
  }
  if mode == "discard" {
    @mqtt.discard_durable_delivery(
      options,
      config,
      required("D1_DELIVERY_ID"),
    )
    log("discarded")
    return
  }
  @mqtt.with_durable_client(options, config, async fn(client, recovered) {
    let handle = if recovered.is_empty() {
      let expiry = @env.now().reinterpret_as_int64() +
        @string.from_str(
          @env.get_env_var("D1_TTL_MS").unwrap_or("60000"),
        )
      client.submit_durable_delivery(
        @env.get_env_var("D1_DELIVERY_ID").unwrap_or("durable-1"),
        @env.get_env_var("D1_TOPIC").unwrap_or("durable/recover"),
        @utf8.encode(@env.get_env_var("D1_PAYLOAD").unwrap_or("payload")),
        expiry,
      )
    } else {
      log("recovered=\{recovered.length()}")
      recovered[0]
    }
    if mode == "crash_after_admit" {
      hard_exit(86)
    }
    let admitted = handle.status()
    log(
      "active id=\{admitted.delivery_id} packet=\{admitted.packet_id} attempts=\{admitted.attempts}",
    )
    let terminal = handle.wait()
    match terminal.phase {
      @mqtt.Acknowledged =>
        log(
          "acknowledged id=\{terminal.delivery_id} packet=\{terminal.packet_id} attempts=\{terminal.attempts}",
        )
      @mqtt.TerminalNotSent(reason) =>
        log("not_sent reason=\{reason} cause=\{@debug.to_string(terminal.terminal_cause)}")
      @mqtt.TerminalOutcomeUnknown(reason) =>
        log("unknown reason=\{reason} cause=\{@debug.to_string(terminal.terminal_cause)}")
      _ => fail("durable handle wait returned a nonterminal phase")
    }
  }) catch {
    @mqtt.DurableStorage(cause) => {
      log("storage-error=\{@debug.to_string(cause)}")
      raise @mqtt.DurableStorage(cause)
    }
    error => raise error
  }
}
