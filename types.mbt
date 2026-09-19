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
/// Network framing used for MQTT bytes. TLS is configured independently, so
/// `WebSocket(path)` with `TlsMode::Plain` is WS and with a TLS mode is WSS.
pub(all) enum NetworkTransport {
  Tcp
  WebSocket(String)
} derive(Eq, @debug.Debug)

///|
/// PEM client certificate presented during a TLS handshake.
/// `certificate_chain_file` is the leaf followed by any intermediates.
/// `private_key_file` must be an unencrypted PEM key. Files are re-read on
/// every new connection and never logged.
pub(all) struct ClientIdentity {
  certificate_chain_file : String
  private_key_file : String
}

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
  DeliveryRejected(String)
  BrokerSessionLost(String)
  DeliveryAttemptsExhausted(String)
  /// A durable outbox operation failed. The nested error preserves whether
  /// operator action should address contention, capacity, integrity, identity,
  /// or I/O before the outbox is reopened.
  DurableStorage(DurableStoreError)
  /// The request's total `operation_timeout_ms` expired. Raised through the
  /// request as `NotSent` (never written) or `OutcomeUnknown` (write started).
  OperationTimeout(String)
  /// A single socket write exceeded `write_timeout_ms`.
  WriteTimeout(String)
  /// PINGREQ completed its write but no PINGRESP arrived within
  /// `ping_response_timeout_ms`.
  PingResponseTimeout(String)
  /// TLS handshake or certificate verification failed after a valid config.
  TlsFailure(String)
} derive(@debug.Debug)

///|
/// Read-only runtime snapshot returned by `Client::stats`. It contains counters
/// and queue occupancy only: no credentials, no message bodies, and no peer
/// address beyond the reason text the client already reports through
/// `Disconnected`.
pub(all) struct ClientStats {
  /// Connection generation; increments on every successful CONNACK.
  generation : Int
  connected : Bool
  /// Business writes waiting to be written (`send_capacity` bound).
  business_queue_depth : Int
  /// Protocol control packets waiting to be written (`control_capacity` bound).
  control_queue_depth : Int
  /// Application events waiting to be consumed (`receive_capacity` bound).
  event_queue_depth : Int
  /// Requests allocated but not yet completed.
  pending_requests : Int
  /// Cumulative successful reconnects since the client was created.
  reconnects : Int
  /// Cumulative disconnects observed; each one starts a new generation.
  disconnects : Int
  /// Requests that finished as `OutcomeUnknown`: written, never acknowledged.
  unknown_outcomes : Int
  /// Reason of the most recent disconnect, if any.
  last_disconnect : String?
}

///|
pub(all) struct Config {
  host : String
  port : Int
  client_id : String
  session_policy : SessionPolicy
  transport : NetworkTransport
  tls : TlsMode
  client_identity : ClientIdentity?
  username : String?
  password : Bytes?
  will : Will?
  keep_alive_secs : Int
  connect_timeout_ms : Int
  /// Total time from request acceptance to completion, covering queueing, the
  /// write and waiting for the acknowledgement.
  operation_timeout_ms : Int
  /// Time allowed for one socket write of an already-dequeued request.
  write_timeout_ms : Int
  /// Time allowed for PINGRESP after PINGREQ has completed its write.
  ping_response_timeout_ms : Int
  send_capacity : Int
  receive_capacity : Int
  max_inflight : Int
  max_packet_size : Int
  reconnect_delay_ms : Int
  max_reconnect_delay_ms : Int
  reconnect_attempts : Int
  /// Reserved slots for protocol control packets (PUBACK, PINGREQ, DISCONNECT)
  /// that business traffic may never occupy.
  control_capacity : Int
}

///|
pub fn Config::new(
  host : String,
  client_id : String,
  port? : Int = 1883,
  session_policy? : SessionPolicy = CleanSession,
  transport? : NetworkTransport = Tcp,
  tls? : TlsMode = Plain,
  client_identity? : ClientIdentity? = None,
  username? : String? = None,
  password? : Bytes? = None,
  will? : Will? = None,
  keep_alive_secs? : Int = 30,
  connect_timeout_ms? : Int = 5000,
  operation_timeout_ms? : Int = 5000,
  write_timeout_ms? : Int = 5000,
  ping_response_timeout_ms? : Int = 5000,
  send_capacity? : Int = 64,
  receive_capacity? : Int = 128,
  max_inflight? : Int = 32,
  max_packet_size? : Int = 65536,
  reconnect_delay_ms? : Int = 250,
  max_reconnect_delay_ms? : Int = 5000,
  reconnect_attempts? : Int = 10,
  control_capacity? : Int = 16,
) -> Config {
  {
    host,
    port,
    client_id,
    session_policy,
    transport,
    tls,
    client_identity,
    username,
    password,
    will,
    keep_alive_secs,
    connect_timeout_ms,
    operation_timeout_ms,
    write_timeout_ms,
    ping_response_timeout_ms,
    send_capacity,
    receive_capacity,
    max_inflight,
    max_packet_size,
    reconnect_delay_ms,
    max_reconnect_delay_ms,
    reconnect_attempts,
    control_capacity,
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
fn valid_http_host(host : String) -> Bool {
  let bytes = @utf8.encode(host)
  if bytes.length() > 1024 {
    return false
  }
  for byte in bytes {
    let value = byte.to_int()
    if value < 0x21 ||
      value > 0x7e ||
      byte is (b'/' | b'?' | b'#' | b'@') {
      return false
    }
  }
  true
}

///|
fn valid_websocket_path(path : String) -> Bool {
  let bytes = @utf8.encode(path)
  if bytes.length() == 0 || bytes.length() > 8192 || bytes[0] != b'/' {
    return false
  }
  for byte in bytes {
    let value = byte.to_int()
    // Request targets are emitted verbatim. Require an ASCII URI with all
    // non-ASCII and unsafe characters percent-encoded by the caller. Fragments
    // are client-side URI syntax and must never be sent to the server.
    if value < 0x21 || value > 0x7e || byte == b'#' {
      return false
    }
  }
  true
}

///|
fn Config::validate(self : Config) -> Unit raise ClientError {
  if self.host.is_empty() || self.client_id.is_empty() {
    raise InvalidConfig("host and stable client_id must be non-empty")
  }
  match self.transport {
    Tcp => ()
    WebSocket(path) => {
      if !valid_http_host(self.host) {
        raise InvalidConfig(
          "WebSocket host must be a bounded ASCII authority without URI delimiters",
        )
      }
      if !valid_websocket_path(path) {
        raise InvalidConfig(
          "WebSocket path must be a bounded ASCII request target beginning with /; percent-encode non-ASCII bytes",
        )
      }
    }
  }
  if self.port <= 0 || self.port > 65535 {
    raise InvalidConfig("invalid port")
  }
  if self.keep_alive_secs < 0 || self.keep_alive_secs > 65535 {
    raise InvalidConfig("keep_alive_secs must be 0..65535")
  }
  if self.connect_timeout_ms <= 0 ||
    self.operation_timeout_ms <= 0 ||
    self.write_timeout_ms <= 0 ||
    self.ping_response_timeout_ms <= 0 ||
    self.send_capacity <= 0 ||
    self.receive_capacity <= 0 ||
    self.control_capacity <= 0 ||
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
  // ResumeSession counts the initial attachment in addition to reconnects.
  // Reject only the one Int value whose attachment budget is unrepresentable;
  // CleanSession retains its existing accepted range.
  if self.session_policy is ResumeSession &&
    self.reconnect_attempts == 0x7FFFFFFF {
    raise InvalidConfig(
      "ResumeSession reconnect_attempts is too large to represent its attachment budget",
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
  if self.client_identity is Some(_) && self.tls is Plain {
    raise InvalidConfig("client identity requires TLS")
  }
}
