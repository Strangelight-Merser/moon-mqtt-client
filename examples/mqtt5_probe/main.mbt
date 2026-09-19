///|
/// Isolated codec interoperability probe, not the production session runtime.
async fn read_frame(reader : &@io.Reader) -> @five.Packet {
  let prefix = reader.read_exactly(1)
  let frame : Array[Byte] = [prefix[0]]
  let mut length = 0
  let mut factor = 1
  for i in 0..<4 {
    let byte = reader.read_exactly(1)[0]
    frame.push(byte)
    length += (byte.to_int() & 127) * factor
    if length + frame.length() > 65536 { abort("oversized broker frame") }
    if byte.to_int() < 128 { break }
    if i == 3 { abort("malformed broker frame") }
    factor *= 128
  }
  for byte in reader.read_exactly(length) { frame.push(byte) }
  @five.decode(Bytes::from_array(frame), 65536)
}

///|
async fn main {
  let args = @env.args()
  guard args.length() == 2 else { abort("expected broker port") }
  let port = @string.parse_int(args[1])
  @async.with_timeout(8000, async fn() {
    let socket = @socket.Tcp::connect_to_host("127.0.0.1", port~)
    defer socket.close()
    let connect : @codec.Connect = {
      client_id: "native5-probe", username: None, password: None,
      will_topic: None, will_message: None, keep_alive: 10,
      flags: { username: false, password: false, will_retain: false, will_qos: @codec.QoS0, will_flag: false, clean_session: true },
    }
    socket.write(@five.encode(@codec.ConnectPacket(connect), [@five.ReceiveMaximum(2), @five.MaximumPacketSize(65536L), @five.TopicAliasMaximum(0)], 65536))
    assert_true(read_frame(socket) is @five.Connack(false, 0, _))
    socket.write(@five.encode(@codec.SubscribePacket({ message_id: 1, topics: [{ topic: "codec/reply", qos: @codec.QoS1 }] }), [], 65536))
    assert_true(read_frame(socket) is @five.Suback(1, [1], _))
    socket.write(@five.encode(@codec.PublishPacket({ topic: "codec/request", payload: b"native5", qos: @codec.QoS1, message_id: Some(2), retain: false, dup: false }), [@five.MessageExpiry(30L), @five.ResponseTopic("codec/reply"), @five.CorrelationData(b"\x00\xff"), @five.UserProperty("origin", "native")], 65536))
    let mut acknowledged = false
    let mut received = false
    while !acknowledged || !received {
      match read_frame(socket) {
        @five.Puback(2, reason, _) if reason == 0 || reason == 16 => acknowledged = true
        @five.Publish(message, properties) => {
          assert_eq(message.topic, "codec/reply")
          assert_eq(message.payload, b"paho5")
          assert_true(properties.contains(@five.CorrelationData(b"\x00\xff")))
          assert_true(properties.contains(@five.UserProperty("origin", "paho")))
          guard message.message_id is Some(id) else { abort("expected QoS1 reply") }
          socket.write(@five.encode(@codec.PubackPacket({ message_id: id }), [], 65536))
          received = true
        }
        _ => abort("unexpected probe broker response")
      }
    }
    socket.write(@five.encode(@codec.DisconnectPacket, [], 65536))
    println("MQTT5 codec Mosquitto/Paho roundtrip passed")
  })
}
