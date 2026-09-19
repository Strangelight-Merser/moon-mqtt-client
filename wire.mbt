///|
fn encode(packet : @codec.Packet, limit : Int) -> Bytes raise ClientError {
  match @codec.encode_packet(packet) {
    Err(e) => raise ProtocolError(e)
    Ok(data) => {
      if data.length() > limit {
        raise ProtocolError("outgoing packet exceeds limit")
      }
      Bytes::from_array(data)
    }
  }
}

///|
priv enum IncomingPacket {
  Incoming311(@codec.Packet)
  Incoming5(@mqtt5.Packet)
}

///|
fn mqtt5_error(error : Error) -> ClientError {
  ProtocolError(error.to_string())
}

///|
fn publish_properties_wire(
  properties : PublishProperties,
  expiry_at_ms : Int64?,
  now_ms : Int64,
) -> Array[@mqtt5.Property] raise ClientError {
  let result : Array[@mqtt5.Property] = []
  match expiry_at_ms {
    Some(deadline) => {
      if deadline <= now_ms {
        raise DeliveryRejected("message expiry elapsed before socket write")
      }
      result.push(@mqtt5.MessageExpiry((deadline - now_ms) / 1000L))
    }
    None => ()
  }
  match properties.response_topic {
    Some(value) => result.push(@mqtt5.ResponseTopic(value))
    None => ()
  }
  match properties.correlation_data {
    Some(value) => result.push(@mqtt5.CorrelationData(value))
    None => ()
  }
  for pair in properties.user_properties {
    result.push(@mqtt5.UserProperty(pair.0, pair.1))
  }
  result
}

///|
fn publish_properties_public(
  properties : Array[@mqtt5.Property],
) -> PublishProperties {
  let mut expiry : Int64? = None
  let mut response : String? = None
  let mut correlation : Bytes? = None
  let users : Array[(String, String)] = []
  for property in properties {
    match property {
      @mqtt5.MessageExpiry(value) => expiry = Some(value)
      @mqtt5.ResponseTopic(value) => response = Some(value)
      @mqtt5.CorrelationData(value) => correlation = Some(value[:].to_owned())
      @mqtt5.UserProperty(key, value) => users.push((key, value))
      _ => ()
    }
  }
  PublishProperties::new(
    message_expiry_secs=expiry,
    response_topic=response,
    correlation_data=correlation,
    user_properties=users,
  )
}

///|
fn broker_reason(
  code : Int,
  properties : Array[@mqtt5.Property],
) -> BrokerReason {
  let mut reason_string : String? = None
  for property in properties {
    if property is @mqtt5.ReasonString(value) {
      reason_string = Some(value)
    }
  }
  { code, reason_string, }
}

///|
fn encode_wire(
  protocol : ProtocolVersion,
  packet : @codec.Packet,
  properties : Array[@mqtt5.Property],
  limit : Int,
) -> Bytes raise ClientError {
  match protocol {
    Mqtt311 => {
      if !properties.is_empty() {
        raise InvalidConfig("MQTT 5 properties require protocol=Mqtt5")
      }
      encode(packet, limit)
    }
    Mqtt5 =>
      @mqtt5.encode(packet, properties, limit) catch {
        error => raise mqtt5_error(error)
      }
  }
}

///|
fn decode_wire(
  protocol : ProtocolVersion,
  bytes : Bytes,
  limit : Int,
) -> IncomingPacket raise ClientError {
  match protocol {
    Mqtt311 =>
      match @codec.decode_packet(bytes.to_array()) {
        Ok(packet) => Incoming311(packet)
        Err(reason) => raise ProtocolError(reason)
      }
    Mqtt5 =>
      Incoming5(@mqtt5.decode(bytes, limit)) catch {
        error => raise mqtt5_error(error)
      }
  }
}

///|
fn Config::connect_bytes(self : Config) -> Bytes raise ClientError {
  let properties : Array[@mqtt5.Property] = match self.protocol {
    Mqtt311 => []
    Mqtt5 =>
      [
        @mqtt5.SessionExpiry(self.session_expiry_secs),
        @mqtt5.ReceiveMaximum(self.max_inflight),
        @mqtt5.MaximumPacketSize(self.max_packet_size.to_int64()),
      ]
  }
  encode_wire(
    self.protocol,
    self.connect_packet(),
    properties,
    self.max_packet_size,
  )
}

///|
/// Reference stream parser for one complete frame.
///
/// The live client does not call this: `read_session_packet` in `runtime.mbt`
/// owns the socket path because it must survive idle slices between chunks
/// without losing the bytes it already read. This function stays as the single
/// place where the frame-shape rules (remaining-length canonical form, packet
/// size limit, decode errors) are stated and unit-tested over a plain reader.
async fn read_packet(reader : &@io.Reader, limit : Int) -> @codec.Packet {
  let header = reader.read_exactly(1)
  let data : Array[Byte] = [header[0]]
  let mut size = 0
  let mut multiplier = 1
  for index in 0..<4 {
    let digit = reader.read_exactly(1)[0]
    data.push(digit)
    size += (digit.to_int() & 127) * multiplier
    if size + data.length() > limit {
      raise ProtocolError("incoming packet exceeds limit")
    }
    if digit.to_int() < 128 {
      if index > 0 && digit == 0 {
        raise ProtocolError("noncanonical remaining length")
      }
      break
    }
    if index == 3 {
      raise ProtocolError("malformed remaining length")
    }
    multiplier *= 128
  }
  let body = reader.read_exactly(size)
  for byte in body {
    data.push(byte)
  }
  match @codec.decode_packet(data) {
    Ok(packet) => packet
    Err(reason) => raise ProtocolError(reason)
  }
}

///|
async fn read_wire_packet(
  reader : &@io.Reader,
  protocol : ProtocolVersion,
  limit : Int,
) -> IncomingPacket {
  let header = reader.read_exactly(1)
  let data : Array[Byte] = [header[0]]
  let mut size = 0
  let mut multiplier = 1
  for index in 0..<4 {
    let digit = reader.read_exactly(1)[0]
    data.push(digit)
    size += (digit.to_int() & 127) * multiplier
    if size + data.length() > limit {
      raise ProtocolError("incoming packet exceeds limit")
    }
    if digit.to_int() < 128 {
      if index > 0 && digit == 0 {
        raise ProtocolError("noncanonical remaining length")
      }
      break
    }
    if index == 3 {
      raise ProtocolError("malformed remaining length")
    }
    multiplier *= 128
  }
  for byte in reader.read_exactly(size) {
    data.push(byte)
  }
  decode_wire(protocol, Bytes::from_array(data), limit)
}

///|
fn Config::connect_packet(self : Config) -> @codec.Packet {
  @codec.ConnectPacket({
    client_id: self.client_id,
    username: self.username,
    password: self.password,
    will_topic: self.will.map(w => w.topic),
    will_message: self.will.map(w => w.payload),
    keep_alive: self.keep_alive_secs,
    flags: {
      username: self.username is Some(_),
      password: self.password is Some(_),
      will_retain: self.will.map(w => w.retain).unwrap_or(false),
      will_qos: self.will.map(w => w.qos.wire()).unwrap_or(@codec.QoS0),
      will_flag: self.will is Some(_),
      clean_session: self.session_policy is CleanSession,
    },
  })
}
