///|
fn valid_reason(kind : Int, reason : Int) -> Bool {
  match kind {
    2 => [0,128,129,130,131,132,133,134,135,136,137,138,140,144,149,151,153,154,155,156,157,159].contains(reason)
    4 => [0,16,128,131,135,144,145,151,153].contains(reason)
    9 => [0,1,2,128,131,135,143,145,151,158,161,162].contains(reason)
    11 => [0,17,128,131,135,143,145].contains(reason)
    14 => [0,128,129,130,131,135,137,139,141,142,143,144,147,148,149,150,151,152,153,154,155,156,157,158,159,160,161,162].contains(reason)
    _ => false
  }
}

///|
fn Cursor::packet_id(self : Cursor) -> Int raise CodecError {
  let id = self.u16()
  if id == 0 { raise Malformed("zero packet identifier") }
  id
}

///|
pub fn decode(bytes : Bytes, limit : Int) -> Packet raise CodecError {
  if bytes.length() > limit || limit < 2 { raise Malformed("packet exceeds configured bound") }
  let cursor : Cursor = { bytes, position: 0, end: bytes.length() }
  let header = cursor.byte()
  let kind = header >> 4
  let length = cursor.variable()
  if length != cursor.end - cursor.position { raise Malformed("Remaining Length mismatch") }
  if kind != 3 && (header & 15) != 0 { raise Malformed("invalid fixed-header flags") }
  let packet = match kind {
    2 => {
      let flags = cursor.byte()
      let reason = cursor.byte()
      if flags > 1 || !valid_reason(2, reason) || (reason != 0 && flags != 0) {
        raise Malformed("invalid CONNACK flags or reason")
      }
      Connack(flags == 1, reason, cursor.properties(2, true))
    }
    3 => {
      let qos = (header >> 1) & 3
      if qos == 3 || (qos == 0 && (header & 8) != 0) { raise Malformed("invalid PUBLISH flags") }
      if qos == 2 { raise Unsupported("QoS 2 is not supported") }
      let topic = cursor.string()
      valid_topic(topic)
      let message_id = if qos == 1 { Some(cursor.packet_id()) } else { None }
      let properties = cursor.properties(3, true)
      let payload = bytes[cursor.position:cursor.end].to_owned()
      cursor.position = cursor.end
      if properties.contains(PayloadFormat(1)) {
        ignore(@utf8.decode(payload) catch { _ => raise Malformed("payload is not valid UTF-8") })
      }
      Publish({ topic, payload, qos: if qos == 1 { @codec.QoS1 } else { @codec.QoS0 }, message_id, retain: (header & 1) != 0, dup: (header & 8) != 0 }, properties)
    }
    4 => {
      let id = cursor.packet_id()
      let reason = if cursor.position == cursor.end { 0 } else { cursor.byte() }
      if !valid_reason(4, reason) { raise Malformed("invalid PUBACK reason") }
      let properties = if cursor.position == cursor.end { [] } else { cursor.properties(4, true) }
      Puback(id, reason, properties)
    }
    9 | 11 => {
      let id = cursor.packet_id()
      let properties = cursor.properties(kind, true)
      let reasons : Array[Int] = []
      while cursor.position < cursor.end {
        let reason = cursor.byte()
        if !valid_reason(kind, reason) { raise Malformed("invalid acknowledgement reason") }
        reasons.push(reason)
      }
      if reasons.is_empty() { raise Malformed("empty acknowledgement reason list") }
      if kind == 9 { Suback(id, reasons, properties) } else { Unsuback(id, reasons, properties) }
    }
    13 => Pingresp
    14 => {
      let reason = if cursor.position == cursor.end { 0 } else { cursor.byte() }
      if !valid_reason(14, reason) { raise Malformed("invalid server DISCONNECT reason") }
      let properties = if cursor.position == cursor.end { [] } else { cursor.properties(14, true) }
      Disconnect(reason, properties)
    }
    15 => raise Unsupported("Enhanced AUTH is not supported")
    5 | 6 | 7 => raise Unsupported("QoS 2 is not supported")
    _ => raise Malformed("unexpected server control packet")
  }
  if cursor.position != cursor.end { raise Malformed("trailing packet bytes") }
  packet
}
