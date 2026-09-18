# Reconnect-resilient QoS 1 decision

Status: approved for implementation by the root technical lead on 2026-09-18 under the user's expanded roadmap instruction. W1 is the integration prerequisite; isolated implementation may proceed while its independent hosted failures are resolved. This is an in-memory capability, not crash durability or device execution confirmation.

## Public behavior

- Add an explicit session policy, defaulting to the existing clean-session behavior. An opt-in resume policy connects with CleanSession=false and uses the configured stable client ID.
- Add explicit `submit_delivery` admission returning a delivery handle. It is available only under the resume policy. Admission requires a connected/ready client and is bounded by the existing inflight/send/packet limits; this milestone adds no general offline queue.
- Ordinary `publish` retains its current behavior in the default clean policy. Under the new resume policy, QoS 1 calls to ordinary `publish` are rejected before any packet is sent, directing callers to explicit recoverable admission. QoS 0 stays ephemeral. This prevents an existing call from quietly gaining background retransmission semantics.
- Each delivery has an application-facing stable DeliveryId, independent of its MQTT packet identifier and connection generation. Accept a caller-provided bounded nonempty ID and reject duplicate active IDs. Provide a native-entropy ID generator if needed by examples; never use a fixed fallback. IDs are caller-owned identities, not a deduplication guarantee after completion/process exit.
- A handle exposes its ID and explainable state: queued, awaiting ACK, awaiting reconnect, acknowledged, or terminal not-sent/unknown-outcome with the initiating cause. Expose enough attempt/packet/generation information to diagnose retransmission without exposing credentials.
- Waiting for a delivery does not own its protocol lifetime. Cancelling or timing out one wait leaves admitted delivery state and the client running. Scope shutdown or explicit disconnect terminates remaining handles honestly (not sent if never started, outcome unknown otherwise).
- PUBACK is the completion criterion. It does not establish device execution or exactly-once application delivery.

## Protocol and state ownership

Connection generation continues to identify one live connection. LogicalSessionIdentity identifies the broker endpoint/client-ID session across those generations. DeliveryId identifies business admission independently of both. Packet identifiers belong to unresolved protocol exchanges, not caller waits.

Extract a small delivery/protocol state component while implementing recovery; do not rewrite TCP/TLS/WS or introduce a generic engine framework. Keep admission, start, acknowledgement, disconnection, reattachment and terminal-state transitions explicit. Runtime I/O applies those transitions and owns the tasks.

For an established logical session, reconnect may replay retained QoS 1 deliveries only when CONNACK confirms Session Present. Preserve original packet identifiers. Re-send in original send order; use DUP for a delivery whose write had started, while a never-started queued delivery remains a first send. Reserve those identifiers before any restoration/new request can allocate them. Do not implement retransmission by recursively calling publish.

If a reconnect reports Session Present=false, stop recovery with a typed broker-session-loss cause; do not silently create new physical commands. Remaining handles become not-sent or outcome-unknown according to whether writing ever started. The application can query/reconcile and explicitly establish a new logical session. A first connection with Session Present=false is an ordinary new session. This milestone does not claim recovery of client memory lost in a process restart, even if a broker retains state.

QoS 1 may duplicate application messages. Resume reconnect with Session Present=true retains broker subscriptions and must not issue duplicate restoration SUBSCRIBEs. Clean mode retains existing restoration. The first Resume connection accepts either Session Present value, claims recovery only for new scope admissions, and requires exclusive client-ID ownership. Reserve delivery packet IDs independently of transient request capacity; any setup requests must skip those identifiers. Handle limits as small as max_inflight=1/send_capacity=1 without deadlock, ID collision or silently enlarged queues. Restored subscriptions/new operations must not steal unresolved delivery identifiers. Invalid/unknown ACKs stay terminal; late ACKs from a dead connection cannot complete new state.

Use `operation_timeout_ms` as a per-attempt PUBACK watchdog starting after the full PUBLISH write completes, with a separate bounded queue/write budget. A single generation watchdog observes delivery records. A watchdog expiry ends the generation and triggers recovery, not a waiter-owned terminal transition. Limit each delivery to `reconnect_attempts + 1` generation attachments including its initial attachment, counting even generations lost before writing. Exhaustion closes the generation and ends the logical scope with a typed cause before releasing packet IDs; supervisor retry counters alone are insufficient because they reset on readiness.

Admission remains atomic/nonblocking, while replay of previously admitted records uses bounded queue backpressure and preserves ordering. `max_inflight` remains a bound on current-generation tracked business requests, including existing QoS0 behavior; this milestone does not silently widen capacity or refactor unrelated ordinary request identity. Packet allocation must skip every retained delivery identifier. Transport loss preserves recoverable state within the scope. Protocol/backpressure terminal failures, exhausted retries and scope exit complete handles exactly once. No active delivery or tombstone collection may grow without a configured bound. Completed handles may be held by their caller, but the engine releases payload/active-table ownership once complete.

## Required evidence

- Deterministic fake-broker test: accept a publish, drop the connection before PUBACK, resume with Session Present=true, observe the same packet ID and DUP, then ACK and observe one stable handle complete.
- Broker-session-loss test: reconnect Session Present=false, no automatic replay, explicit terminal state distinguishing never-sent versus possibly-sent work.
- Wait cancellation followed by a later successful ACK, without killing/replacing the connection.
- Packet-ID reservation and retransmission ordering with small capacities, including subscription restoration and queue pressure.
- Terminal malformed ACK and normal scoped cleanup, with all handles settled and no leaked task/socket.
- Independent broker reconnect interoperability; native state tests alone do not prove broker behavior.
- Existing publish/cancel/timeout/TCP/TLS/WS assertions remain in effect. Public API, docs and state reflect the final implementation.

Reference: MQTT 3.1.1 sections 3.1.2.4, 4.1, 4.4 and 4.6. Persistent-session retransmission reuses original packet identifiers and original send order: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html . The broker-session-loss policy closes the scope instead of continuing automatically into a replacement session.
