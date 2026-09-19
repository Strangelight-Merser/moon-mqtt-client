///|
fn bad(bytes : Bytes) -> Unit raise {
  try {
    ignore(decode(bytes, 65536))
    abort("malformed input was accepted")
  } catch {
    Malformed(_) => ()
    other => raise other
  }
}

///|
test "compact acknowledgements preserve success and rejection codes" {
  assert_true(decode(b"\x40\x02\x00\x09", 32) is Puback(9, 0, []))
  assert_true(decode(b"\x40\x03\x00\x09\x87", 32) is Puback(9, 0x87, []))
  assert_true(decode(b"\x40\x04\x00\x09\x10\x00", 32) is Puback(9, 0x10, []))
  assert_true(decode(b"\xe0\x00", 32) is Disconnect(0, []))
  assert_true(decode(b"\xe0\x01\x8e", 32) is Disconnect(0x8e, []))
  bad(b"\xe0\x01\x04") // Only a client may request Will publication.
  bad(b"\x40\x03\x00\x09\x01")
  bad(b"\x40\x02\x00\x00")
}

///|
test "CONNACK legal optional fields are consumed and singleton constraints checked" {
  let packet = decode(b"\x20\x12\x00\x00\x0f\x13\x00\x05\x24\x01\x25\x01\x21\x00\x02\x27\x00\x01\x00\x00", 64)
  match packet {
    Connack(false, 0, properties) => {
      assert_true(properties.contains(ServerKeepAlive(5)))
      assert_true(properties.contains(ReceiveMaximum(2)))
      assert_true(properties.contains(MaximumPacketSize(65536L)))
    }
    _ => abort("expected CONNACK")
  }
  bad(b"\x20\x09\x00\x00\x06\x21\x00\x01\x21\x00\x02")
  bad(b"\x20\x06\x00\x00\x03\x21\x00\x00")
  bad(b"\x20\x05\x00\x00\x02\x24\x02")
  bad(b"\x20\x03\x01\x87\x00")
  bad(b"\xe0\x07\x00\x05\x11\x00\x00\x00\x01")
}

///|
test "remaining lengths property lengths and UTF-8 have independent bounds" {
  for bytes in [b"", b"\x20", b"\x20\x80\x00", b"\x20\x03\x00\x00\x01", b"\xd0\x01\x00", b"\x21\x03\x00\x00\x00", b"\x30\x05\x00\x01\x00\x00\x61", b"\x30\x05\x00\x01\xff\x00\x61"] {
    bad(bytes)
  }
  bad(b"\x20\x04\x00\x00\x01\x7f")
  try { ignore(decode(b"\x20\x03\x00\x00\x00", 4)); abort("bound bypassed") } catch { Malformed(_) => (); other => raise other }
}

///|
test "publication preserves repeatable metadata order and binary correlation" {
  let publish : @codec.Publish = { topic: "request", payload: b"data", qos: @codec.QoS1, message_id: Some(42), retain: false, dup: true }
  let properties = [MessageExpiry(30L), ResponseTopic("reply"), CorrelationData(b"\x00\xff"), UserProperty("x", "one"), UserProperty("x", "two")]
  let bytes = encode(@codec.PublishPacket(publish), properties, 256)
  match decode(bytes, 256) {
    Publish(actual, metadata) => {
      assert_eq(actual.message_id, Some(42))
      assert_true(actual.dup)
      assert_eq(actual.payload, b"data")
      assert_eq(metadata, properties)
    }
    _ => abort("expected PUBLISH")
  }
  try { ignore(encode(@codec.PublishPacket(publish), [ReceiveMaximum(1)], 256)); abort("invalid direction accepted") } catch { Malformed(_) => (); other => raise other }
  try { ignore(encode(@codec.PublishPacket(publish), properties, 20)); abort("metadata ignored in bound") } catch { Malformed(_) => (); other => raise other }
}

///|
test "CONNECT inserts version and both property-length sections before payload" {
  let connect : @codec.Connect = {
    client_id: "c", username: None, password: None,
    will_topic: Some("w"), will_message: Some(b"x"), keep_alive: 10,
    flags: { username: false, password: false, will_retain: false, will_qos: @codec.QoS1, will_flag: true, clean_session: false },
  }
  let bytes = encode(@codec.ConnectPacket(connect), [SessionExpiry(60L)], 128)
  assert_eq(bytes, b"\x10\x1a\x00\x04MQTT\x05\x0c\x00\x0a\x05\x11\x00\x00\x00\x3c\x00\x01c\x00\x00\x01w\x00\x01x")
  assert_eq(encode(@codec.PingreqPacket, [], 2), b"\xc0\x00")
  assert_eq(encode(@codec.PubackPacket({ message_id: 2 }), [], 4), b"\x40\x02\x00\x02")
}

///|
test "acknowledgement reason arrays remain aligned for runtime validation" {
  assert_true(decode(b"\x90\x05\x00\x07\x00\x01\x87", 32) is Suback(7, [1, 0x87], []))
  assert_true(decode(b"\xb0\x05\x00\x07\x00\x00\x11", 32) is Unsuback(7, [0, 0x11], []))
  bad(b"\x90\x03\x00\x07\x00")
  bad(b"\xb0\x04\x00\x07\x00\x01")
}

///|
test "payload format and unsupported exchanges fail explicitly" {
  let packet = @codec.PublishPacket({ topic: "t", payload: b"\xff", qos: @codec.QoS0, message_id: None, retain: false, dup: false })
  try { ignore(encode(packet, [PayloadFormat(1)], 64)); abort("invalid UTF-8 payload accepted") } catch { Malformed(_) => (); error => raise error }
  for bytes in [b"\xf0\x00", b"\x34\x00", b"\x30\x07\x00\x01t\x03\x23\x00\x01"] {
    try { ignore(decode(bytes, 64)); abort("unsupported packet accepted") } catch { Unsupported(_) => (); error => raise error }
  }
}
