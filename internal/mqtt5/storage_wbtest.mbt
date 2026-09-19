///|
test "outbox metadata preserves binary correlation and ordered repeated properties" {
  let properties = [
    MessageExpiry(30L), ResponseTopic("app/reply"), CorrelationData(b"\x00\xff"),
    UserProperty("same", "first"), UserProperty("same", "second"),
  ]
  let bytes = encode_publish_properties(properties, 512)
  assert_eq(decode_publish_properties(bytes, 512), properties)
  try encode_publish_properties(properties, bytes.length() - 1) catch {
    Malformed(_) => ()
    _ => fail("unexpected error category")
  } noraise { _ => fail("over-bound metadata accepted") }
}

///|
test "recovered metadata rejects trailing, malformed and illegal-direction values" {
  for bytes in [b"\x00\x00", b"\x02\x08", b"\x03\x21\x00\x01", b"\x02\x0b\x01"] {
    try decode_publish_properties(bytes, 32) catch {
      _ => ()
    } noraise { _ => fail("malformed metadata accepted") }
  }
  try decode_publish_properties(b"\x00", 0) catch {
    _ => ()
  } noraise { _ => fail("zero storage bound accepted") }
  try encode_publish_properties([MessageExpiry(1L), MessageExpiry(2L)], 32) catch {
    _ => ()
  } noraise { _ => fail("duplicate singleton metadata accepted") }
}
