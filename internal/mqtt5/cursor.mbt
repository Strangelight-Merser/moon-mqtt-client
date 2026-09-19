///|
priv struct Cursor {
  bytes : Bytes
  mut position : Int
  end : Int
}

///|
fn Cursor::byte(self : Cursor) -> Int raise CodecError {
  if self.position >= self.end { raise Malformed("truncated packet") }
  let value = self.bytes[self.position].to_int()
  self.position += 1
  value
}

///|
fn Cursor::u16(self : Cursor) -> Int raise CodecError {
  let high = self.byte()
  high * 256 + self.byte()
}

///|
fn Cursor::u32(self : Cursor) -> Int64 raise CodecError {
  let high = self.u16().to_int64()
  high * 65536L + self.u16().to_int64()
}

///|
fn Cursor::variable(self : Cursor) -> Int raise CodecError {
  let mut value = 0
  let mut factor = 1
  for index in 0..<4 {
    let digit = self.byte()
    value += (digit & 127) * factor
    if digit < 128 {
      if index > 0 && digit == 0 { raise Malformed("noncanonical variable byte integer") }
      return value
    }
    factor *= 128
  }
  raise Malformed("oversized variable byte integer")
}

///|
fn Cursor::binary(self : Cursor) -> Bytes raise CodecError {
  let length = self.u16()
  if length > self.end - self.position { raise Malformed("truncated binary field") }
  let value = self.bytes[self.position:self.position + length].to_owned()
  self.position += length
  value
}

///|
fn Cursor::string(self : Cursor) -> String raise CodecError {
  let value = @utf8.decode(self.binary()) catch { _ => raise Malformed("invalid UTF-8") }
  validate_text(value)
  value
}

///|
fn Cursor::properties(self : Cursor, kind : Int, incoming : Bool) -> Array[Property] raise CodecError {
  let size = self.variable()
  if size > self.end - self.position { raise Malformed("truncated properties") }
  let section : Cursor = { bytes: self.bytes, position: self.position, end: self.position + size }
  let values : Array[Property] = []
  while section.position < section.end {
    let property = match section.variable() {
      0x01 => PayloadFormat(section.byte())
      0x02 => MessageExpiry(section.u32())
      0x03 => ContentType(section.string())
      0x08 => ResponseTopic(section.string())
      0x09 => CorrelationData(section.binary())
      0x0b => SubscriptionIdentifier(section.variable())
      0x11 => SessionExpiry(section.u32())
      0x12 => AssignedClientIdentifier(section.string())
      0x13 => ServerKeepAlive(section.u16())
      0x15 => AuthenticationMethod(section.string())
      0x16 => AuthenticationData(section.binary())
      0x17 => RequestProblemInformation(section.byte())
      0x18 => WillDelay(section.u32())
      0x19 => RequestResponseInformation(section.byte())
      0x1a => ResponseInformation(section.string())
      0x1c => ServerReference(section.string())
      0x1f => ReasonString(section.string())
      0x21 => ReceiveMaximum(section.u16())
      0x22 => TopicAliasMaximum(section.u16())
      0x23 => TopicAlias(section.u16())
      0x24 => MaximumQoS(section.byte())
      0x25 => RetainAvailable(section.byte())
      0x26 => UserProperty(section.string(), section.string())
      0x27 => MaximumPacketSize(section.u32())
      0x28 => WildcardSubscriptionAvailable(section.byte())
      0x29 => SubscriptionIdentifiersAvailable(section.byte())
      0x2a => SharedSubscriptionAvailable(section.byte())
      _ => raise Malformed("unknown property identifier")
    }
    values.push(property)
  }
  validate_properties(values, kind, incoming)
  self.position = section.end
  values
}
