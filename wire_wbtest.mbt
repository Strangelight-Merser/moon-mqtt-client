///|
async fn packet_reader_rejects(data : Bytes, limit : Int) -> Bool {
  let reader = @io.MemoryReader(async fn(writer) { writer.write(data) })
  defer reader.close()
  try {
    ignore(read_packet(reader, limit))
    false
  } catch {
    _ => true
  }
}

///|
async test "stream reader rejects malicious, oversized and truncated remaining lengths" {
  // Four continuation bytes are invalid: MQTT remaining length has at most
  // four bytes, and the fourth must terminate the field.
  assert_true(packet_reader_rejects(b"\x30\xff\xff\xff\x80", 268435455))
  // Multi-byte zero is non-canonical and must not be accepted as length zero.
  assert_true(packet_reader_rejects(b"\x30\x80\x00", 1024))
  // The declared body alone exceeds the configured total packet limit.
  assert_true(packet_reader_rejects(b"\x30\x7f", 32))
  // The stream ends before all three declared body bytes arrive.
  assert_true(packet_reader_rejects(b"\x30\x03\x00", 1024))
}

///|
async test "stream reader consumes exactly one packet from concatenated input" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\xc0\x00\xd0\x00")
  })
  defer reader.close()
  match read_packet(reader, 16) {
    @codec.PingreqPacket => ()
    _ => abort("expected first PINGREQ packet")
  }
  match read_packet(reader, 16) {
    @codec.PingrespPacket => ()
    _ => abort("expected second PINGRESP packet")
  }
}

///|
async test "QoS 1 publish preserves binary payload and packet id through stream" {
  let original = @codec.PublishPacket({
    topic: "sensors/binary",
    payload: b"\x00\xff\x10payload",
    qos: @codec.QoS1,
    message_id: Some(321),
    retain: true,
    dup: true,
  })
  let bytes = encode(original, 1024)
  let reader = @io.MemoryReader(async fn(writer) { writer.write(bytes) })
  defer reader.close()
  match read_packet(reader, 1024) {
    @codec.PublishPacket(publish) => {
      assert_eq(publish.topic, "sensors/binary")
      assert_eq(publish.payload, b"\x00\xff\x10payload")
      assert_eq(publish.qos, @codec.QoS1)
      assert_eq(publish.message_id, Some(321))
      assert_true(publish.retain)
      assert_true(publish.dup)
    }
    _ => abort("expected QoS 1 PUBLISH packet")
  }
}
