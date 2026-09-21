///|
fn required(name : String) -> String raise {
  @env.get_env_var(name).unwrap_or_else(() => fail("missing \{name}"))
}

///|
async fn main {
  let protocol = match required("RECOVERY_PROTOCOL") {
    "mqtt311" => @mqtt.Mqtt311
    "mqtt5" => @mqtt.Mqtt5
    value => fail("unsupported protocol \{value}")
  }
  let config = @mqtt.Config::new(
    required("RECOVERY_HOST"),
    required("RECOVERY_CLIENT_ID"),
    port=@string.from_str(required("RECOVERY_PORT")),
    protocol=protocol,
    session_policy=@mqtt.ResumeSession,
    session_expiry_secs=@string.from_str(required("RECOVERY_SESSION_EXPIRY")),
  )
  let options = @mqtt.DurableOutboxOptions::new(
    required("RECOVERY_OUTBOX"),
    max_records=4,
    max_payload_bytes=65536L,
    max_pages=128,
  )
  let records = @mqtt.inspect_durable_outbox(options, config)
  @stdio.stdout.write(@utf8.encode("records=\{records.length()}\n"))
}
