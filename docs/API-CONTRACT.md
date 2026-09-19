# Runtime contract (v0.2.0)

This document is the normative description of what `moon-mqtt-client` promises
about timeouts, queueing, cancellation and failure classification. The README is
an introduction; this file is what the tests assert.

## Timeouts

Three independent budgets, all in milliseconds, all defaulting to 5000:

| Field | Clock starts | Clock ends | On expiry |
|---|---|---|---|
| `operation_timeout_ms` | A request is accepted by `publish`/`subscribe`/`unsubscribe` | Its completion (ACK read or final error) | Session is aborted; every pending request settles |
| `write_timeout_ms` | One socket write begins for a dequeued request | That write returns | Session is aborted and the write loop stops |
| `ping_response_timeout_ms` | The PINGREQ write **completed** | PINGRESP is read | Session is aborted with `PingResponseTimeout` |

`operation_timeout_ms` deliberately includes queueing time. A request that waits
in the send queue for longer than the budget is never written: it finishes as
`NotSent` so a caller can safely retry a request that was definitely not sent.
The deadline watchdog wakes the request condition normally; it does not raise
from a task-group child. The request coroutine performs the deadline check and
session abort, so a caught timeout cannot poison the caller's next async call.

There is no legacy `ack_timeout_ms` field and no compatibility shim; the old name
described only the final phase, and callers that meant "total budget" were
silently getting a longer wait than intended.

## Queues and reserved control slots

- Business traffic is bounded by `send_capacity` (default 64) and by
  `max_inflight` (default 32).
- Protocol control packets (PUBACK, PINGREQ, DISCONNECT) use a **separate**
  bounded queue of `control_capacity` slots (default 16). Business traffic can
  never occupy those slots, so a saturated business queue cannot starve
  acknowledgements or heartbeats.
- Control packets are written before queued business traffic.
- If the control queue itself is exhausted, the client aborts the connection
  with `ProtocolError` instead of dropping a control packet. The client does not
  promise to stay online on a connection that cannot make progress.
- DISCONNECT still completes when the business queue and inflight table are full,
  because it travels through the reserved slots.

## Queue occupancy in diagnostics

`Client.stats()` reports occupancy from mirrors maintained at the enqueue and
dequeue points, because the async queue type exposes no length. The mirrors are
zeroed whenever the session aborts and discards queued items. The snapshot is
best-effort and does not claim cross-field atomicity.

## Failure classification

| Result | Meaning | Was a write attempted? |
|---|---|---|
| Success (QoS 0) | The socket write completed | Yes |
| Success (QoS 1) | A PUBACK for that identifier arrived on that connection | Yes |
| `NotSent(reason)` | The request never reached a socket write | No |
| `OutcomeUnknown(reason)` | A write started and no acknowledgement resolved it | Yes |
| `Backpressure(reason)` | Capacity (queue or inflight) rejected the request before any write | No |
| `OperationTimeout` | The total budget expired (delivered as `NotSent`/`OutcomeUnknown`) | Depends |
| `WriteTimeout` | A single socket write exceeded `write_timeout_ms` | Yes |
| `PingResponseTimeout` | A written PINGREQ was never answered | Yes |
| `ConnectionRefused` / `ProtocolError` | Connect-phase rejection or protocol violation | n/a |
| `ReconnectExhausted` | Retries after a previously working connection were exhausted | n/a |
| `InvalidConfig` | Identity files missing, encrypted, malformed, or mismatched; or `Plain` plus a client identity | n/a |
| `TlsFailure` | TLS handshake or server-certificate verification failed after a valid config | n/a |
| `DurableStorage(error)` | A required SQLite transition failed; the nested error retains busy/full/read-only/corrupt/I/O/schema/identity/limit/state classification | Depends; inspect the stable ID before reopening |
| `Closed` | The client was shut down normally (`disconnect` or `with_client` scope end) | n/a |

A failed first connection raises the classified error to `wait_connected` and to
`with_client`. `Closed` is reserved for a normal stop; it does not replace
`InvalidConfig` or `TlsFailure`.

For ordinary requests, `OutcomeUnknown` is the result that requires
application-level recovery. Durable blocked rows and `DurableStorage` likewise
require inspection and reconciliation. Recovery must not assume success or
failure: query the peer or resend an idempotent request.

## Recoverable QoS 1 delivery

`Config.session_policy` defaults to `CleanSession`, which preserves the
ordinary request behavior described above. `ResumeSession` sends
`CleanSession=false` and enables `Client::submit_delivery`. This explicit API
admits one in-memory QoS 1 delivery and returns a stable `DeliveryHandle`.
Ordinary QoS 1 `publish` is rejected under `ResumeSession`; QoS 0 remains an
ephemeral ordinary request.

Admission is connected-only, atomic and nonblocking. It uses the existing
`max_inflight` and `send_capacity` limits and requires a caller-provided,
nonempty UTF-8 delivery ID of at most 128 encoded bytes. Active IDs cannot be
reused. The ID is application identity for this process scope; it is not a
durable deduplication record.

The handle reports `Queued`, `AwaitingAck`, `AwaitingReconnect`,
`Acknowledged`, `TerminalNotSent`, or `TerminalOutcomeUnknown`, plus its MQTT
packet ID, connection-attempt count and current generation. A failed terminal
status also carries `terminal_cause: ClientError?`, so applications can match
typed causes such as `BrokerSessionLost` and `DeliveryAttemptsExhausted`
without parsing the phase's diagnostic string. Cancelling or timing out
`DeliveryHandle::wait` only stops that wait. The admitted protocol operation
continues and may be observed again through the same handle.

After a transport loss, a reconnect with `Session Present=true` reattaches
unresolved deliveries in admission order with their original packet IDs. A
delivery whose socket write began is retransmitted with `DUP=1`; one that never
started remains a first send. Packet IDs stay reserved across generations and
ordinary request allocation skips them. Resume reconnect does not duplicate
SUBSCRIBE because the broker retained the session.

`Session Present=false` on a Resume reconnect ends the logical scope with
`BrokerSessionLost`; deliveries that never began a write become
`TerminalNotSent`, while deliveries that may have reached the broker become
`TerminalOutcomeUnknown`. The first Resume connection accepts either Session
Present value and only tracks admissions made in the current process scope.
Applications must use exclusive client-ID ownership; this library does not
recover work from a previous process.

For recoverable delivery, every generation attachment gets a separate
`operation_timeout_ms` budget to begin its socket write. This bounds both an
admitted queue entry and a replay waiting for a queue slot. Once writing begins,
`write_timeout_ms` owns the socket-write budget. After the complete PUBLISH
write, a fresh `operation_timeout_ms` PUBACK watchdog starts; no deadline is
inherited from an earlier phase or generation. Expiry reconnects while the
attachment budget remains. A delivery may attach to at most `reconnect_attempts + 1`
generations, counting a generation lost before writing; exhaustion ends the
logical scope with `DeliveryAttemptsExhausted`. Transport reconnect exhaustion
remains `ReconnectExhausted`. Replay uses the bounded send queue rather than an
unbounded offline queue.

PUBACK completes the delivery. It does not prove downstream processing,
device execution, or exactly-once application delivery. MQTT QoS 1 replay can
produce duplicate application messages. Completion and terminal failure remove
the active payload and packet-ID reservation; callers may retain the small
terminal handle snapshot.

## Durable outbox beta

`with_durable_client(options, config, callback)` extends only the explicit
recoverable QoS 1 path with one concrete SQLite outbox. It requires
`ResumeSession`; ordinary QoS 0 remains ephemeral, ordinary QoS 1 remains
rejected under resume mode, and the in-memory `submit_delivery` API is rejected
inside a durable scope. The outbox file is exclusively owned for the callback
scope and is bound to a versioned identity containing `mqtt`/`mqtts`/`ws`/`wss`
scheme, host, port, WebSocket path, stable client ID and optional username.
Passwords, CA paths, certificate paths and private-key paths are not stored, so
credential rotation does not by itself create a new logical session. The
application remains responsible for ensuring the broker maps rotated
certificates to the same tenant and that the stable client ID has one owner.

Before any dial, the scope opens and integrity-checks SQLite, applies absolute
expiry, loads pending rows in insertion order, and reserves every stored packet
ID. The callback runs after the connection is ready and receives the exact
recovered `DeliveryHandle` objects. A recovered row that may have reached the
network requires `Session Present=true` even on this process's first connection.
If the broker lost that logical session, the row is persisted as blocked and no
PUBLISH is replayed. A recovered row proven never started may make its first
send after a new session is established.

`submit_durable_delivery(id, topic, payload, expires_at_ms)` is async because
admission commits before a success handle exists. It remains connected-only and
bounded by `max_inflight`; `DurableOutboxOptions` separately bounds rows, total
payload bytes and SQLite main-file pages. A generation attachment commits its
attempt before queueing. Before the first possible socket write, the outbox
commits `ever_started`; after a valid PUBACK, DELETE commits before the handle is
woken and its packet ID or payload is released. Cancellation waits for any
already-submitted SQLite transition and publishes the matching memory ownership
before it propagates. A caller uncertain whether admission returned can inspect
the stable ID.

The absolute expiry is Unix time in milliseconds. Expired never-started rows are
removed as not sent. Expired rows that might have been sent are blocked as an
unknown outcome and require application reconciliation. Wall-clock corrections
can move when this absolute deadline is observed. Per-generation queue and ACK
watchdogs still use the monotonic runtime clock.

Normal `disconnect` or callback exit settles current handles with `Closed` but
keeps unresolved rows pending for a later explicit durable scope. Protocol
failure, broker-session loss and exhausted delivery or reconnect budgets persist
a blocked reason before current handles are settled. A storage failure stops the
scope without a same-scope network retry; if the blocked marker itself could not
be committed, the durable store error is the authoritative result. SQLite is
closed only after client tasks and protected transitions have ended.

`inspect_durable_outbox` returns records in stable insertion order without
dialing. `discard_durable_delivery` deletes only a row proven never started and
only outside an active owning scope. Started or blocked possible-write rows have
no single-row force-delete API in this beta.

The unavoidable duplicate window is a broker PUBACK that arrived before the
durable DELETE committed. A crash in that interval leaves the row eligible for
retransmission with its original packet ID and `DUP=1`. This is at-least-once
transport recovery, not exactly-once processing or a permanent deduplication
ledger. SQLite `synchronous=EXTRA`, rollback journal mode and process-kill tests
do not establish guarantees beyond SQLite, the operating system and filesystem.

## Connection lifetime, timeouts and cancellation

The read loop owns the connection lifetime. It assembles each MQTT frame with
bounded one-byte reads, preserving the partially assembled frame between idle
slices. This lets it observe a caller-side abort without cancelling a read that
has already consumed part of a packet. The supervisor is therefore never
cancelled by its own children, and every generation end is reported as a normal
disconnect or a transport error that it can classify.

## Cancellation

Ordinary `publish`, `subscribe`, and `unsubscribe` keep the conservative rule:
**cancelling an unfinished request aborts the current connection.** After the
write may have reached the peer, the
client cannot know whether the request was executed, so it ends the generation
rather than leaving an ambiguous request alive while a new request reuses the
identifier space. Consequences:

- Every pending request is settled exactly once, as `NotSent` or
  `OutcomeUnknown`; none is left hanging.
- A request abandoned while still queued is never written, even if the write loop
  dequeues it later; it is already settled as `NotSent`.
- Identifiers are never reused across generations, so a late ACK from generation
  N can never complete a request created in generation N+1.
- Callers that need a longer wait than `operation_timeout_ms` must not wrap
  `publish` in a shorter timeout; the operation budget is the contract, and the
  client's own timeout already classifies the result.

## Reconnection

- `CleanSession` is the default. Every reconnect is a new broker session that
  restores confirmed subscriptions before `Connected(generation)` is emitted.
- `ResumeSession` retains unresolved explicit deliveries in memory and requires
  `Session Present=true` before replay on a reconnect. It relies on the broker's
  persistent session and skips duplicate subscription restoration.
- Backoff is bounded exponential (`reconnect_delay_ms` growing by 1.5x up to
  `max_reconnect_delay_ms`) plus jitter drawn from a **per-client** xorshift
  stream. On native targets, each default stream is seeded from four bytes of
  operating-system entropy. If that entropy source is unavailable, construction
  stays available by mixing the current millisecond timestamp with a
  process-local counter. This fallback varies sequential constructions in one
  process, but it is weaker than operating-system entropy; 32-bit seed collisions
  remain possible, especially across processes started in the same millisecond.
  `Client::set_reconnect_seed` pins the stream for deterministic tests. Clients
  normally avoid the lockstep retries caused by the previous fixed default seed.
- Terminal conditions end the client instead of retrying forever:
  `Backpressure` (event queue overflow), `ProtocolError` (including a saturated
  control queue and unexpected server packets), `NotConnected` during the connect
  phase, and `ReconnectExhausted` after the attempts budget.
- A protocol failure after CONNACK remains terminal, including an invalid ACK
  during subscription restoration. The session retains the original typed cause
  until the supervisor classifies it; cleanup and a pending operation's
  `OutcomeUnknown` do not replace that cause. Pending operations still settle as
  `NotSent` or `OutcomeUnknown` according to whether their write started.
- Ordinary transport loss, request cancellation and operation timeout keep their
  existing reconnect behavior. A socket write timeout is recorded as
  `WriteTimeout`; other writer errors retain their original I/O cause.
- State is never inherited across generations: `Client.stats()` resets queue and
  inflight counts when a session ends, and `last_disconnect` records why.

## Diagnostics

`Client.stats()` returns generation, connection state, business/control/event
queue occupancy, pending requests, cumulative reconnects, cumulative
disconnects, cumulative unknown outcomes, and the most recent disconnect reason.
It contains no credentials and no message bodies, and the client does not depend
on any monitoring service to produce it.

## Native WebSocket transport

`Config.transport` selects `Tcp` (default) or `WebSocket(path)`. TLS remains a separate setting: `Plain` gives WS, while `SystemRoots`/`CustomCA` gives WSS with the existing optional client identity. Existing publish completion semantics do not change. PUBACK never means a downstream device executed a command.

The upgrade offers only `mqtt` and requires that exact selected subprotocol. Response headers are bounded to 16 KiB. The path starts with `/`, is at most 8192 encoded ASCII bytes, and contains no whitespace, control bytes or fragment; callers percent-encode non-ASCII URI bytes. HTTP host validation applies only to WebSocket transport. No compression or other extensions are negotiated. Server text messages, masked frames, malformed upgrades or invalid framing are terminal protocol failures. Detected framing violations immediately close the transport without awaiting a Close-frame write, so a blocked peer cannot mask the original error; after a successful MQTT DISCONNECT, the client gives brokers such as EMQX up to `write_timeout_ms` to process DISCONNECT and close the connection, answering a received WebSocket Close through the reader. It then closes the transport unconditionally. Sending an immediate WebSocket Close before the broker processes queued MQTT frames can discard QoS 0 traffic on EMQX; no fixed delay or business-message replay is used. Ordinary transport loss remains subject to the existing reconnect policy.

Binary data messages form one MQTT byte stream: a packet may cross frame/message boundaries and multiple packets may share a message. Stream buffering stays bounded, and the MQTT packet-size bound is enforced before accepting an oversized packet body. Peer control frames are handled by the transport independently of MQTT packet boundaries.

## MQTT 5 runtime subset

`Config.protocol` is explicit and defaults to `Mqtt311`. MQTT 3.1.1 rejects a
nonzero `session_expiry_secs`; MQTT 5 requires zero for `CleanSession` and a
nonzero value for `ResumeSession`. MQTT 5 frames are decoded only by the MQTT 5
decoder. No adapter passes MQTT 5 bytes through the MQTT 3.1.1 decoder.

Outgoing publish properties are copied before suspension. Message Expiry is
converted once to an absolute deadline, then recomputed immediately before the
socket write, including after durable storage commits. Properties count toward
the negotiated packet limit and, for durable rows, toward the configured
payload-plus-property byte bound (the canonical empty property section costs
zero metadata bytes; every nonempty section is charged in full). Recovered rows
retain the same deadline.

Detailed subscription/unsubscription operations return every per-topic reason,
including mixed success and rejection. The Unit unsubscribe wrapper raises
`BrokerRejected` if any topic was rejected; only successful removals update the
desired subscription set. A rejection does not terminate the reader.

Negative PUBACK completes an ordinary request as `BrokerRejected`. For durable
work, the outbox DELETE commits before the packet identifier, payload and handle
are released; the handle reaches `Rejected(BrokerReason)`. Like a positive
PUBACK, this does not create permanent completion history, and a process crash
after receiving the ACK but before DELETE can replay the row.

Receive Maximum limits QoS 1 publications awaiting PUBACK on the current
connection. Local max_inflight bounds admitted work; queued and recovered work
waits for credit in FIFO order. A parked queue head still occupies its bounded
slot. The separate bounded control queue remains available for PUBACK, PINGREQ and DISCONNECT. Maximum Packet Size,
Maximum QoS, Retain Available and Server Keep Alive constrain the current
generation. A server DISCONNECT ends that scope with `ServerDisconnected` and
is not retried in the same scope. A first `Session Present=true` is accepted
only when local known-session evidence exists; durable evidence is a committed
metadata bit independent of whether the outbox contains rows.

The WS parser is not periodically cancelled while a connection remains usable. Session abort or scope cancellation closes the underlying stream and ends the reader; no partially consumed frame is reused in a later generation. A publish remains NotSent until its writer starts and OutcomeUnknown after writing starts without completion. Transport masking uses secure entropy and fails if entropy is unavailable; the weaker reconnect-jitter fallback is never used for masking.
