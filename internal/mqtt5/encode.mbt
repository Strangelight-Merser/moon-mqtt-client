///|
fn append(out : Array[Byte], bytes : BytesView) -> Unit {
  for byte in bytes { out.push(byte) }
}

///|
fn put_variable(out : Array[Byte], number : Int) -> Unit raise CodecError {
  if number < 0 || number > 268435455 { raise Malformed("variable byte integer out of range") }
  let mut value = number
  while true {
    let digit = value & 127
    value = value >> 7
    out.push((if value == 0 { digit } else { digit | 128 }).to_byte())
    if value == 0 { break }
  }
}

///|
fn put_u16(out : Array[Byte], value : Int) -> Unit {
  out.push((value >> 8).to_byte())
  out.push((value & 255).to_byte())
}

///|
fn put_u32(out : Array[Byte], value : Int64) -> Unit {
  put_u16(out, (value >> 16).to_int())
  put_u16(out, (value & 65535L).to_int())
}

///|
fn put_binary(out : Array[Byte], value : Bytes) -> Unit raise CodecError {
  if value.length() > 65535 { raise Malformed("binary field too large") }
  put_u16(out, value.length())
  append(out, value[:])
}

///|
fn put_string(out : Array[Byte], value : String) -> Unit raise CodecError {
  validate_text(value)
  put_binary(out, @utf8.encode(value))
}

///|
fn property_bytes(properties : Array[Property], kind : Int, limit : Int) -> Bytes raise CodecError {
  validate_properties(properties, kind, false)
  let body : Array[Byte] = []
  for property in properties {
    if body.length() >= limit { raise Malformed("properties exceed packet bound") }
    put_variable(body, property.identifier())
    match property {
      PayloadFormat(v) | RequestProblemInformation(v) | RequestResponseInformation(v) |
      MaximumQoS(v) | RetainAvailable(v) | WildcardSubscriptionAvailable(v) |
      SubscriptionIdentifiersAvailable(v) | SharedSubscriptionAvailable(v) => body.push(v.to_byte())
      ReceiveMaximum(v) | ServerKeepAlive(v) | TopicAliasMaximum(v) | TopicAlias(v) => put_u16(body, v)
      MessageExpiry(v) | SessionExpiry(v) | WillDelay(v) | MaximumPacketSize(v) => put_u32(body, v)
      SubscriptionIdentifier(v) => put_variable(body, v)
      ContentType(v) | ResponseTopic(v) | AssignedClientIdentifier(v) | ResponseInformation(v) |
      ServerReference(v) | ReasonString(v) | AuthenticationMethod(v) => put_string(body, v)
      CorrelationData(v) | AuthenticationData(v) => put_binary(body, v)
      UserProperty(key, value) => { put_string(body, key); put_string(body, value) }
    }
  }
  if body.length() > limit { raise Malformed("properties exceed packet bound") }
  let result : Array[Byte] = []
  put_variable(result, body.length())
  for byte in body { result.push(byte) }
  Bytes::from_array(result)
}

///|
/// Reuse MQTT 3.1.1 field validation and payload encoding, but explicitly insert
/// MQTT 5 property sections and protocol level. No inbound packet uses this
/// transformation or the older decoder.
pub fn encode(packet : @codec.Packet, properties : Array[Property], limit : Int) -> Bytes raise CodecError {
  match packet {
    @codec.PublishPacket(publish) => if publish.payload.length() > limit { raise Malformed("payload exceeds packet bound") }
    @codec.ConnectPacket(connect) => if connect.will_message is Some(payload) && payload.length() > limit { raise Malformed("Will exceeds packet bound") }
    _ => ()
  }
  if packet is @codec.PublishPacket(publish) && properties.contains(PayloadFormat(1)) {
    ignore(@utf8.decode(publish.payload) catch { _ => raise Malformed("payload is not valid UTF-8") })
  }
  let legacy = match @codec.encode_packet(packet) {
    Ok(bytes) => Bytes::from_array(bytes)
    Err(reason) => raise Malformed(reason)
  }
  if legacy.length() > limit { raise Malformed("outgoing packet exceeds bound") }
  let cursor : Cursor = { bytes: legacy, position: 0, end: legacy.length() }
  let header = cursor.byte()
  ignore(cursor.variable())
  let start = cursor.position
  let kind = header >> 4
  let properties = property_bytes(properties, kind, limit)
  let body : Array[Byte] = []
  match packet {
    @codec.ConnectPacket(connect) => {
      validate_text(connect.client_id)
      if connect.username is Some(username) { validate_text(username) }
      if connect.will_topic is Some(topic) { valid_topic(topic) }
      if connect.flags.will_qos == @codec.QoS2 { raise Unsupported("QoS 2 Will is unsupported") }
      // MQTT protocol name/level/flags/keepalive occupy ten bytes.
      append(body, legacy[start:start + 6])
      body.push(b'\x05')
      append(body, legacy[start + 7:start + 10])
      append(body, properties[:])
      cursor.position = start + 10
      ignore(cursor.string()) // Client Identifier, validated and length bounded.
      append(body, legacy[start + 10:cursor.position])
      if connect.flags.will_flag { body.push(b'\x00') } // no Will properties
      append(body, legacy[cursor.position:])
    }
    @codec.PublishPacket(publish) => {
      valid_topic(publish.topic)
      if publish.qos == @codec.QoS2 { raise Unsupported("QoS 2 is unsupported") }
      ignore(cursor.string())
      if publish.qos == @codec.QoS1 { ignore(cursor.packet_id()) }
      append(body, legacy[start:cursor.position])
      append(body, properties[:])
      append(body, legacy[cursor.position:])
    }
    @codec.SubscribePacket(subscribe) => {
      for topic in subscribe.topics {
        validate_text(topic.topic)
        if topic.topic.has_prefix("$share/") { raise Unsupported("Shared subscriptions are unsupported") }
        if topic.qos == @codec.QoS2 { raise Unsupported("QoS 2 subscription is unsupported") }
      }
      append(body, legacy[start:start + 2])
      append(body, properties[:])
      append(body, legacy[start + 2:])
    }
    @codec.UnsubscribePacket(unsubscribe) => {
      for topic in unsubscribe.topics { validate_text(topic) }
      append(body, legacy[start:start + 2])
      append(body, properties[:])
      append(body, legacy[start + 2:])
    }
    @codec.PubackPacket(_) => {
      append(body, legacy[start:])
      if properties.length() > 1 { body.push(b'\x00'); append(body, properties[:]) }
    }
    @codec.DisconnectPacket => {
      if properties.length() > 1 { body.push(b'\x00'); append(body, properties[:]) }
    }
    @codec.PingreqPacket => {
      if properties.length() != 1 { raise Malformed("PINGREQ has no properties") }
    }
    _ => raise Unsupported("unsupported outgoing MQTT 5 packet")
  }
  let out : Array[Byte] = [header.to_byte()]
  put_variable(out, body.length())
  if body.length() > limit - out.length() { raise Malformed("outgoing packet exceeds bound") }
  for byte in body { out.push(byte) }
  Bytes::from_array(out)
}
