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

`OutcomeUnknown` is the only result that requires application-level recovery.
Recovery must not assume success or failure: query the peer or resend an
idempotent request.

## Connection lifetime, timeouts and cancellation

The read loop owns the connection lifetime. It reads with a bounded wait so it
can always observe a caller-side abort and end the generation normally; closing
the transport underneath an unbounded blocking read would surface as a
coroutine cancellation, and a cancelled task inside a task group would poison
the supervisor coroutine and silently stop reconnection. The supervisor is
therefore never cancelled by its own children, and every generation end is
reported as a normal disconnect or a transport error that it can classify.

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
  stream. `Client::set_reconnect_seed` pins the stream for deterministic tests.
  Two clients that lose the same broker no longer retry in lockstep, which the
  previous generation-indexed formula caused.
- Terminal conditions end the client instead of retrying forever:
  `Backpressure` (event queue overflow), `ProtocolError` (including a saturated
  control queue and unexpected server packets), `NotConnected` during the connect
  phase, and `ReconnectExhausted` after the attempts budget.
- State is never inherited across generations: `Client.stats()` resets queue and
  inflight counts when a session ends, and `last_disconnect` records why.

## Diagnostics

`Client.stats()` returns generation, connection state, business/control/event
queue occupancy, pending requests, cumulative reconnects, cumulative
disconnects, cumulative unknown outcomes, and the most recent disconnect reason.
It contains no credentials and no message bodies, and the client does not depend
on any monitoring service to produce it.
