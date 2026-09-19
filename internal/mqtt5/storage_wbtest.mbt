///|
test "outbox metadata preserves binary correlation and ordered repeated properties" {
  let properties = [
    MessageExpiry(30L), ResponseTopic("app/reply"), CorrelationData(b"\x00\xff"),
    UserProperty("same", "first"), UserProperty("same", "second"),
  ]
  let bytes = encode_publish_properties(properties, 512)
  assert_eq(decode_publish_properties(bytes, 512), properties)
  let too_small = try? encode_publish_properties(properties, bytes.length() - 1)
  assert_true(too_small is Err(Malformed(_)))
}

///|
test "recovered metadata rejects trailing, malformed and illegal-direction values" {
  for bytes in [b"\x00\x00", b"\x02\x08", b"\x03\x21\x00\x01", b"\x02\x0b\x01"] {
    assert_true((try? decode_publish_properties(bytes, 32)) is Err(_))
  }
  assert_true((try? decode_publish_properties(b"\x00", 0)) is Err(_))
  assert_true((try? encode_publish_properties([MessageExpiry(1L), MessageExpiry(2L)], 32)) is Err(_))
}
