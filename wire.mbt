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
      clean_session: true,
    },
  })
}
