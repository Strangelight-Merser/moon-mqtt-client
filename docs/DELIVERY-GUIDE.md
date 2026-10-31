# Choose the delivery lifetime

| Choice | Admission and completion | Recovery boundary |
|---|---|---|
| `publish(topic, payload, qos=AtLeastOnce)` | Call waits for PUBACK; queue time counts toward operation budget | Request belongs to this call; no automatic replay |
| `submit_delivery(id, topic, payload)` then `handle.wait()` | Bounded nonblocking admission; independently observable status | Same client scope; ResumeSession, exclusive stable ID and Session Present gate |
| `submit_durable_delivery(id, topic, payload, expires_at_ms)` | SQLite admission COMMIT before ownership; ACK DELETE COMMIT before completion | Process restart with the same schema-2 store and logical identity |

The [README examples](../README.md#api-示例) include callback scopes and imports
for all three choices. All require a ready connection for new admission; none
is an unbounded offline queue. QoS0 completion means local transport write.
QoS1 acknowledgement means broker acceptance, not a committed consumer
transaction or completed physical action.

`with_client` enters the callback only after first readiness. Initial dial,
negative CONNACK and TLS failures return to the caller. After readiness,
ordinary recovery uses a bounded budget reset on successful readiness. Opt-in
MQTT5 maintenance retries only 0x89/0x8B and has a separate lifetime budget that
does not reset on readiness. See the [failure matrix](FAILURE-MATRIX.md).

Cancelling an ordinary request can abort its generation. Cancelling a delivery
waiter does not cancel admitted delivery. Cancellation during durable admission
does not prove there is no committed row: inspect/reopen using the same identity.
Keep the business ID and absolute expiry for reconciliation. Do not resubmit an
unknown physical command under a new ID without deciding how to handle duplicates.

For diagnosis, record existing `Client::stats()` fields: generation, readiness,
queue depths, pending requests, reconnects, disconnects, unknown outcomes and
last disconnect. Record handle delivery ID, packet ID, attempts, phase and
broker reason. Compare independent broker/application observations. Avoid
logging credentials or raw payloads by default. Receive Maximum pressure does
not create extra queue capacity; protocol control slots remain reserved.

The durable outbox covers **outgoing** publications. Incoming PUBACK does not
wait for a business database transaction. This library provides no durable inbox.

Recovery is operator tooling. `inspect`/`export` preserve evidence;
`resolve`/`resume` are experimental whole-session operations requiring explicit
reconciliation. They never replay old work. Read the
[recovery contract](architecture/DURABLE-RECOVERY.md) before using them.
