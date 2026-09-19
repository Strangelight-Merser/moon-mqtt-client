///|
/// MQTT 3.1.1 remains the default. MQTT 5 must be selected explicitly.
pub(all) enum ProtocolVersion {
  Mqtt311
  Mqtt5
} derive(Eq, @debug.Debug)

///|
/// Bounded MQTT 5 application metadata. The owning client validates the whole
/// encoded packet before admission. Expiry is frozen as an absolute deadline
/// when accepted; queueing and retransmission never restart this interval.
pub(all) struct PublishProperties {
  message_expiry_secs : Int64?
  response_topic : String?
  correlation_data : Bytes?
  user_properties : Array[(String, String)]
} derive(Eq, @debug.Debug)

///|
pub fn PublishProperties::new(
  message_expiry_secs? : Int64? = None,
  response_topic? : String? = None,
  correlation_data? : Bytes? = None,
  user_properties? : Array[(String, String)] = [],
) -> PublishProperties {
  { message_expiry_secs, response_topic, correlation_data, user_properties }
}

///|
fn PublishProperties::owned(self : PublishProperties) -> PublishProperties {
  {
    message_expiry_secs: self.message_expiry_secs,
    response_topic: self.response_topic,
    correlation_data: self.correlation_data.map(value => value[:].to_owned()),
    user_properties: self.user_properties.map(pair => (pair.0, pair.1)),
  }
}

///|
fn PublishProperties::empty() -> PublishProperties {
  PublishProperties::new()
}

///|
/// Numeric protocol reason plus optional broker diagnostic. Broker text is
/// untrusted diagnostic data and never changes endpoint or credentials.
pub(all) struct BrokerReason {
  code : Int
  reason_string : String?
} derive(Eq, @debug.Debug)

///|
/// QoS 0 completes locally; QoS 1 records a valid successful broker PUBACK.
/// Neither result confirms application or physical device execution.
pub(all) enum PublishReceipt {
  Written
  Accepted(BrokerReason)
} derive(Eq, @debug.Debug)

///|
pub(all) struct SubscriptionAcknowledgement {
  result : SubscriptionResult
  reason : BrokerReason
} derive(Eq, @debug.Debug)

///|
/// Negotiated limits for one physical MQTT 5 connection. Local configuration
/// can be stricter; these values are the effective bounds used by the client.
pub(all) struct NegotiatedSettings {
  receive_maximum : Int
  max_packet_size : Int
  maximum_qos : QoS
  retain_available : Bool
  keep_alive_secs : Int
  session_expiry_secs : Int64
} derive(@debug.Debug)
