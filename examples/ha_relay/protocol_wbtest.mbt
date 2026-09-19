///|
test "expired or indefinite physical commands never decode" {
  let command : Command = { id: "id", target: true, expires_at_ms: 200L }
  let bytes = @utf8.encode(command.encode())
  assert_eq(decode_command(bytes, 199L), Some(command))
  assert_eq(decode_command(bytes, 200L), None)
  assert_eq(decode_command(b"{\"id\":\"id\",\"target\":\"ON\"}", 1L), None)
  assert_eq(decode_command(b"{\"id\":\"id\",\"target\":\"ON\",\"expires_at_ms\":3.5}", 1L), None)
  assert_eq(decode_command(bytes, -40000L), None)
}

///|
test "fresh identities and feedback correlation survive process-independent construction" {
  assert_true(fresh_id() != fresh_id())
  let feedback : Feedback = { boot_id: "boot", sequence: 1, state: false, correlation_id: "request" }
  assert_eq(decode_feedback(@utf8.encode(feedback.encode())), Some(feedback))
  assert_eq(decode_feedback(b"{\"boot_id\":\"b\",\"sequence\":1.5,\"state\":\"ON\",\"correlation_id\":\"r\"}"), None)
}
