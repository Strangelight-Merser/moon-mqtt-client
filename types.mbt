///|
pub(all) enum QoS {
  AtMostOnce
  AtLeastOnce
} derive(Eq, @debug.Debug)

///|
pub(all) enum TlsMode {
  Plain
  SystemRoots
  CustomCA(String)
} derive(@debug.Debug)

///|
pub(all) struct Will {
  topic : String
  payload : Bytes
  qos : QoS
  retain : Bool
} derive(@debug.Debug)

///|
pub(all) struct Subscription {
  topic : String
  qos : QoS
} derive(Eq, @debug.Debug)

///|
pub(all) enum SubscriptionResult {
  Granted(QoS)
  Rejected
} derive(Eq, @debug.Debug)

///|
pub(all) struct Message {
  topic : String
  payload : Bytes
  qos : QoS
  retain : Bool
  dup : Bool
  generation : Int
} derive(@debug.Debug)

///|
pub(all) enum Event {
  Connected(Int)
  Disconnected(Int, String)
  MessageReceived(Message)
} derive(@debug.Debug)

///|
pub(all) suberror ClientError {
  InvalidConfig(String)
  ProtocolError(String)
  NotConnected
  Closed
  Backpressure(String)
  NotSent(String)
  OutcomeUnknown(String)
  ConnectionRefused(String)
  ReconnectExhausted(String)
} derive(@debug.Debug)

///|
pub(all) struct Config {
  host : String
  port : Int
  client_id : String
  tls : TlsMode
  username : String?
  password : Bytes?
  will : Will?
  keep_alive_secs : Int
  connect_timeout_ms : Int
  ack_timeout_ms : Int
  send_capacity : Int
  receive_capacity : Int
  max_inflight : Int
  max_packet_size : Int
  reconnect_delay_ms : Int
  max_reconnect_delay_ms : Int
  reconnect_attempts : Int
}

///|
pub fn Config::new(
  host : String,
  client_id : String,
  port? : Int = 1883,
  tls? : TlsMode = Plain,
  username? : String? = None,
  password? : Bytes? = None,
  will? : Will? = None,
  keep_alive_secs? : Int = 30,
  connect_timeout_ms? : Int = 5000,
  ack_timeout_ms? : Int = 5000,
  send_capacity? : Int = 64,
  receive_capacity? : Int = 128,
  max_inflight? : Int = 32,
  max_packet_size? : Int = 65536,
  reconnect_delay_ms? : Int = 250,
  max_reconnect_delay_ms? : Int = 5000,
  reconnect_attempts? : Int = 10,
) -> Config {
  {
    host,
    port,
    client_id,
    tls,
    username,
    password,
    will,
    keep_alive_secs,
    connect_timeout_ms,
    ack_timeout_ms,
    send_capacity,
    receive_capacity,
    max_inflight,
    max_packet_size,
    reconnect_delay_ms,
    max_reconnect_delay_ms,
    reconnect_attempts,
  }
}

///|
fn QoS::wire(self : QoS) -> @codec.QoS {
  match self {
    AtMostOnce => @codec.QoS0
    AtLeastOnce => @codec.QoS1
  }
}

///|
fn Config::validate(self : Config) -> Unit raise ClientError {
  if self.host.is_empty() || self.client_id.is_empty() {
    raise InvalidConfig("host and stable client_id must be non-empty")
  }
  if self.port <= 0 || self.port > 65535 {
    raise InvalidConfig("invalid port")
  }
  if self.keep_alive_secs < 0 || self.keep_alive_secs > 65535 {
    raise InvalidConfig("keep_alive_secs must be 0..65535")
  }
  if self.connect_timeout_ms <= 0 ||
    self.ack_timeout_ms <= 0 ||
    self.send_capacity <= 0 ||
    self.receive_capacity <= 0 ||
    self.max_inflight <= 0 ||
    self.max_inflight > 65535 ||
    self.max_packet_size < 8 ||
    self.max_packet_size > 268435455 ||
    self.reconnect_delay_ms <= 0 ||
    self.max_reconnect_delay_ms < self.reconnect_delay_ms ||
    self.reconnect_attempts < 0 {
    raise InvalidConfig(
      "invalid timeout, capacity, packet limit or reconnect policy",
    )
  }
  if self.will is Some(will) &&
    (
      will.topic.is_empty() ||
      will.topic.contains("+") ||
      will.topic.contains("#")
    ) {
    raise InvalidConfig(
      "Will topic must be a non-empty topic name without wildcards",
    )
  }
  if self.will is Some(will) && will.payload.length() > self.max_packet_size - 4 {
    raise InvalidConfig("Will payload exceeds packet limit")
  }
  if self.password is Some(_) && self.username is None {
    raise InvalidConfig("password requires username")
  }
}
