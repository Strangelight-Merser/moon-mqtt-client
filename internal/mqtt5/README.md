# Native MQTT 5 codec seam

This internal module encodes client CONNECT, PUBLISH, PUBACK, SUBSCRIBE, UNSUBSCRIBE, PINGREQ and DISCONNECT, and decodes server CONNACK, PUBLISH, PUBACK, SUBACK, UNSUBACK, PINGRESP and DISCONNECT. It does not own sessions, reconnection, negotiation enforcement or persistence.

Property variants carry typed values; packet/direction, singleton, range and string validation apply on both sides. Legal optional CONNACK metadata is consumed even when the runtime does not use it. Enhanced AUTH, Topic Alias and Subscription Identifiers produce an explicit unsupported error. QoS 2 is outside the seam. Negative reason codes remain numeric for the runtime to classify; decoder success is not delivery success.

Outgoing encoding reuses the pinned MQTT3 field encoder, explicitly replaces protocol level and inserts MQTT5 property sections, including the empty Will Properties section. Incoming bytes use a separate bounded parser. Frame size is validated before decoding. The production stream reader must also enforce its configured bound before allocating a complete frame. Packet-specific reason counts and negotiated limits belong to runtime validation.

[OASIS MQTT 5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html) is the normative source: properties in sections2.2.2 and3.1–3.14; reason tables3-1,3-4,3-8,3-9,3-10. The native tests cover compact ACKs, malformed lengths/UTF-8, duplicates, direction/range errors, ordered metadata and CONNECT with a Will. `tests/mqtt5_codec.py` runs the isolated native probe against Mosquitto and a Paho5 peer. Neither that probe nor these unit tests establishes production MQTT5 client acceptance; see docs/architecture/MQTT5-SUBSET.md.
