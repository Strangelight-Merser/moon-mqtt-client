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
| `Closed` | The client was shut down normally (`disconnect` or `with_client` scope end) | n/a |

A failed first connection raises the classified error to `wait_connected` and to
`with_client`. `Closed` is reserved for a normal stop; it does not replace
`InvalidConfig` or `TlsFailure`.

`OutcomeUnknown` is the only result that requires application-level recovery.
Recovery must not assume success or failure: query the peer or resend an
idempotent request.

## Connection lifetime, timeouts and cancellation

The read loop owns the connection lifetime. It assembles each MQTT frame with
bounded one-byte reads, preserving the partially assembled frame between idle
slices. This lets it observe a caller-side abort without cancelling a read that
has already consumed part of a packet. The supervisor is therefore never
cancelled by its own children, and every generation end is reported as a normal
disconnect or a transport error that it can classify.

## Cancellation

This version keeps the conservative rule: **cancelling an unfinished request
aborts the current connection.** After the write may have reached the peer, the
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

- `CleanSession=true` only. Every reconnect is a new session that restores
  confirmed subscriptions before `Connected(generation)` is emitted.
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

## Native WebSocket transport (development branch)

`Config.transport` selects `Tcp` (default) or `WebSocket(path)`. TLS remains a separate setting: `Plain` gives WS, while `SystemRoots`/`CustomCA` gives WSS with the existing optional client identity. Existing publish completion semantics do not change. PUBACK never means a downstream device executed a command.

The upgrade offers only `mqtt` and requires that exact selected subprotocol. Response headers are bounded to 16 KiB. The path starts with `/`, is at most 8192 encoded ASCII bytes, and contains no whitespace, control bytes or fragment; callers percent-encode non-ASCII URI bytes. HTTP host validation applies only to WebSocket transport. No compression or other extensions are negotiated. Server text messages, masked frames, malformed upgrades or invalid framing are terminal protocol failures. Detected framing violations immediately close the transport without awaiting a Close-frame write, so a blocked peer cannot mask the original error; normal MQTT DISCONNECT sends a bounded best-effort WebSocket Close. Ordinary transport loss remains subject to the existing reconnect policy.

Binary data messages form one MQTT byte stream: a packet may cross frame/message boundaries and multiple packets may share a message. Stream buffering stays bounded, and the MQTT packet-size bound is enforced before accepting an oversized packet body. Peer control frames are handled by the transport independently of MQTT packet boundaries.

The WS parser is not periodically cancelled while a connection remains usable. Session abort or scope cancellation closes the underlying stream and ends the reader; no partially consumed frame is reused in a later generation. A publish remains NotSent until its writer starts and OutcomeUnknown after writing starts without completion. Transport masking uses secure entropy and fails if entropy is unavailable; the weaker reconnect-jitter fallback is never used for masking.
