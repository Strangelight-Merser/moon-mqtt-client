///|
/// Packet-specific validation is mandatory on both decode and encode.
pub(all) suberror CodecError {
  Malformed(String)
  Unsupported(String)
} derive(Debug)

///|
pub(all) enum Property {
  PayloadFormat(Int)
  MessageExpiry(Int64)
  ContentType(String)
  ResponseTopic(String)
  CorrelationData(Bytes)
  SubscriptionIdentifier(Int)
  SessionExpiry(Int64)
  AssignedClientIdentifier(String)
  ServerKeepAlive(Int)
  AuthenticationMethod(String)
  AuthenticationData(Bytes)
  RequestProblemInformation(Int)
  WillDelay(Int64)
  RequestResponseInformation(Int)
  ResponseInformation(String)
  ServerReference(String)
  ReasonString(String)
  ReceiveMaximum(Int)
  TopicAliasMaximum(Int)
  TopicAlias(Int)
  MaximumQoS(Int)
  RetainAvailable(Int)
  UserProperty(String, String)
  MaximumPacketSize(Int64)
  WildcardSubscriptionAvailable(Int)
  SubscriptionIdentifiersAvailable(Int)
  SharedSubscriptionAvailable(Int)
} derive(Eq, Debug)

///|
pub(all) enum Packet {
  Connack(Bool, Int, Array[Property])
  Publish(@codec.Publish, Array[Property])
  Puback(Int, Int, Array[Property])
  Suback(Int, Array[Int], Array[Property])
  Unsuback(Int, Array[Int], Array[Property])
  Pingresp
  Disconnect(Int, Array[Property])
} derive(Debug)

///|
fn Property::identifier(self : Property) -> Int {
  match self {
    PayloadFormat(_) => 0x01
    MessageExpiry(_) => 0x02
    ContentType(_) => 0x03
    ResponseTopic(_) => 0x08
    CorrelationData(_) => 0x09
    SubscriptionIdentifier(_) => 0x0b
    SessionExpiry(_) => 0x11
    AssignedClientIdentifier(_) => 0x12
    ServerKeepAlive(_) => 0x13
    AuthenticationMethod(_) => 0x15
    AuthenticationData(_) => 0x16
    RequestProblemInformation(_) => 0x17
    WillDelay(_) => 0x18
    RequestResponseInformation(_) => 0x19
    ResponseInformation(_) => 0x1a
    ServerReference(_) => 0x1c
    ReasonString(_) => 0x1f
    ReceiveMaximum(_) => 0x21
    TopicAliasMaximum(_) => 0x22
    TopicAlias(_) => 0x23
    MaximumQoS(_) => 0x24
    RetainAvailable(_) => 0x25
    UserProperty(_, _) => 0x26
    MaximumPacketSize(_) => 0x27
    WildcardSubscriptionAvailable(_) => 0x28
    SubscriptionIdentifiersAvailable(_) => 0x29
    SharedSubscriptionAvailable(_) => 0x2a
  }
}

///|
fn validate_text(value : String) -> Unit raise CodecError {
  if value.length() > 65535 { raise Malformed("UTF-8 field exceeds 65535 code units") }
  if @utf8.encode(value).length() > 65535 { raise Malformed("UTF-8 field exceeds 65535 bytes") }
  for character in value {
    let code = character.to_int()
    if code == 0 || (code >= 0xd800 && code <= 0xdfff) {
      raise Malformed("forbidden MQTT UTF-8 character")
    }
  }
}

///|
fn valid_topic(value : String) -> Unit raise CodecError {
  validate_text(value)
  if value.is_empty() || value.contains("+") || value.contains("#") {
    raise Malformed("invalid topic name")
  }
}

///|
fn allowed_property(id : Int, kind : Int, incoming : Bool) -> Bool {
  // kind=0 denotes Will Properties within CONNECT.
  match kind {
    0 => [1, 2, 3, 8, 9, 24, 38].contains(id)
    1 => [17, 21, 22, 23, 25, 33, 34, 38, 39].contains(id)
    2 => [17, 18, 19, 21, 22, 26, 28, 31, 33, 34, 36, 37, 38, 39, 40, 41, 42].contains(id)
    3 => [1, 2, 3, 8, 9, 35, 38].contains(id) || (incoming && id == 11)
    4 | 9 | 11 => id == 31 || id == 38
    8 => id == 38 || id == 11
    10 => id == 38
    14 => id == 31 || id == 38 || (incoming && id == 28) || (!incoming && id == 17)
    _ => false
  }
}

///|
fn validate_properties(properties : Array[Property], kind : Int, incoming : Bool) -> Unit raise CodecError {
  let seen : Map[Int, Bool] = Map([])
  for property in properties {
    let id = property.identifier()
    if !allowed_property(id, kind, incoming) { raise Malformed("property invalid for packet or direction") }
    if seen.contains(id) && id != 0x26 && !(id == 0x0b && kind == 3 && incoming) {
      raise Malformed("duplicate singleton property")
    }
    seen[id] = true
    match property {
      PayloadFormat(v) | RequestProblemInformation(v) | RequestResponseInformation(v) |
      MaximumQoS(v) | RetainAvailable(v) | WildcardSubscriptionAvailable(v) |
      SubscriptionIdentifiersAvailable(v) | SharedSubscriptionAvailable(v) =>
        if v != 0 && v != 1 { raise Malformed("boolean property is not 0 or 1") }
      ReceiveMaximum(v) => if v < 1 || v > 65535 { raise Malformed("invalid Receive Maximum") }
      ServerKeepAlive(v) | TopicAliasMaximum(v) => if v < 0 || v > 65535 { raise Malformed("invalid two-byte property") }
      TopicAlias(_) => raise Unsupported("Topic Alias was not negotiated")
      SubscriptionIdentifier(v) => {
        if v <= 0 || v > 268435455 { raise Malformed("invalid Subscription Identifier") }
        raise Unsupported("Subscription Identifiers are outside this client subset")
      }
      MessageExpiry(v) | SessionExpiry(v) | WillDelay(v) =>
        if v < 0L || v > 4294967295L { raise Malformed("four-byte property out of range") }
      MaximumPacketSize(v) => if v < 1L || v > 4294967295L { raise Malformed("invalid Maximum Packet Size") }
      ResponseTopic(value) => valid_topic(value)
      AssignedClientIdentifier(value) => {
        validate_text(value)
        if value.is_empty() { raise Malformed("empty Assigned Client Identifier") }
      }
      ContentType(v) | ResponseInformation(v) | ServerReference(v) | ReasonString(v) => validate_text(v)
      UserProperty(key, value) => { validate_text(key); validate_text(value) }
      CorrelationData(v) => if v.length() > 65535 { raise Malformed("binary property too large") }
      AuthenticationMethod(_) | AuthenticationData(_) => raise Unsupported("Enhanced AUTH is not supported")
    }
  }
}
