# MQTT 5 subset boundary

Status: root-approved implementation boundary, 2026-09-18. Network integration follows Q1/D1 acceptance. This is the application-facing subset from roadmap v0.7, not full MQTT 5 or QoS 2.

## Observable capability

Keep MQTT 3.1.1 as the default. Add explicit protocol selection and MQTT 5 session options, with validated Clean Start/Session Expiry behavior. Existing clean policy starts clean with zero expiry; resume policy uses Clean Start=false and a configured nonzero session expiry. A durable store identity includes protocol and session policy, so reopening cannot silently change the logical exchange. A broker shortening expiry is visible to the caller, not ignored.

Expose numeric reason codes with optional diagnostic reason strings for connection refusal, server disconnect, negative PUBACK, subscription and unsubscription results. A valid negative PUBACK ends the exchange as broker rejection, not success, NotSent or ambiguous transport loss. Durable rejection must commit its terminal transition before releasing the identifier or completing the handle. Positive PUBACK 0x10 remains accepted by the broker, while preserving the code for inspection. PUBACK never means device execution.

Add bounded publish metadata: Message Expiry, ordered/repeatable User Properties, Response Topic and binary Correlation Data. Incoming Message preserves these properties. Outgoing properties count toward the packet bound and persist with durable deliveries. Retransmission recomputes remaining expiry from an immutable deadline and never extends it; local command expiry remains authoritative. No permissive fallback silently strips properties when MQTT 3.1.1 is selected.

Honor CONNACK Receive Maximum, Maximum Packet Size, Maximum QoS, Retain Available and Server Keep Alive. Local configured bounds remain upper bounds. Negotiated limits govern both new work and retained replay; reject/quarantine work incompatible with a new connection instead of silently changing QoS, retain or payload. Receive Maximum controls outstanding QoS 1 writes, not the unrelated control queue. Lower negotiated capacity applies backpressure without allocating unbounded waiters or starving PUBACK/control traffic.

## Codec and protocol scope

Implement a small native MQTT 5 packet adapter using the established bounded byte-stream framing. Keep transport/TLS/WS ownership independent. Do not reinterpret MQTT 5 bytes with the MQTT 3.1.1 decoder. Cover CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ/PINGRESP and DISCONNECT. Validate fixed flags, canonical variable-byte integers, UTF-8 and bounded strings/binary values, packet-specific property direction/multiplicity/value rules, packet IDs and reason-code tables. Consume and validate legal server properties even when their optional capability is not exposed; do not misclassify a compliant CONNACK as an unknown-property failure.

Topic Alias, requested Subscription Identifiers, shared subscription semantics, QoS 2 and Enhanced AUTH are outside this milestone. Advertise no incoming alias capability, never originate aliases/identifiers/enhanced-auth properties, and report unsupported exchanges explicitly. Do not add generic property maps that evade validation. Do not follow server redirects automatically or change credentials/endpoints based on server text.

PUBACK Remaining Length 2 defaults reason to success, length 3 includes reason without Property Length. DISCONNECT length 0 defaults reason, length 1 includes reason without Property Length. These legal compact forms need regression coverage. SUBACK/UNSUBACK reason counts must match their requests. Server DISCONNECT reasons must survive generic resource cleanup and determine whether the logical scope stops; no endless retry on an explicit administrative/protocol rejection.

## Evidence gate

Native codec tests must include malformed/truncated/duplicate properties, illegal directions and compact forms. Raw peers must prove negotiated limits, negative ACK semantics and expiry across recovery. Independently exercise MQTT 5 against Mosquitto, including metadata roundtrip and persistent session restart. Repeat original MQTT 3.1.1 and durable recovery checks on integrated bytes. CLI/example paths should make the new subset usable. No version bump or release is implied by implementation.

Normative reference: [OASIS MQTT 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html), especially packet sections 3.1–3.14 and flow control/retransmission. The extraction under `_build/exec-mqtt5/spec-map.md` is a navigation aid, not a replacement for the packet tables. Root verified compact PUBACK and DISCONNECT rules directly against sections 3.4.2 and 3.14.2; the original extraction incorrectly conflated absent properties with absent reason codes.

MQTT 5 first-connection session rule: section3.2.2.1.1 requires closing when Session Present=true but the client has no local Session State. Do not copy Q1's MQTT3 first-connection permissiveness into5. The5 integration must distinguish known logical-session ownership (including persisted durable metadata) from an empty fresh in-memory scope, reject unexpected retained state, and never silently force Clean Start to erase it. This is an explicit new-protocol contract, not a change to default3.1.1 behavior.
