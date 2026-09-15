# Defects found and fixed for v0.2.0

Each entry records the problem, the cause, the fix and how it was verified. The
first four defects were found while building the state-sync demo; they were real
connection-lifecycle bugs, not cosmetic issues.

## D1 — a caller-side abort never reconnected

**Problem.** When an operation timed out or a request was cancelled, the session
was aborted but the supervisor task stayed parked. The next connection was never
attempted: `wait_connected()` eventually failed instead of waiting through a
reconnect. The independent fault injector case `timeout_reconnect` failed, and a
minimal two-connection fake peer reproduced it deterministically.

**Cause.** The supervisor body waited on a condition variable for
`session.alive` to become false. A task-group child that raises cannot wake a
group body parked on `Cond::wait`, and the read loop had been changed to swallow
its transport error and return normally, so nothing woke the body either.

**Fix.** The read loop again owns the connection lifetime and the body waits on
its task, so a transport failure propagates directly. It reads one byte per
bounded operation and preserves the partially assembled MQTT frame across
100 ms idle slices, so a caller-side abort can be observed without discarding
bytes already consumed from the socket.

**Verification.** `tests/protocol_faults.py` case `timeout_reconnect` passes
again (late ACK after close, new connection, reused identifier 1, real
`E0 00`). The demo's `broker_restart` and `lost_puback` scenarios pass.

## D2 — a timed-out request poisoned the caller's coroutine

**Problem.** After a request hit `operation_timeout_ms`, the caller's next
asynchronous operation failed immediately with `Cancelled`, which also broke the
reconnect path above.

**Cause.** In `moonbitlang/async@0.21.3`, a task-group child spawned with the
default `allow_failure=false` changes the group to `Fail` when it raises and
cancels every sibling, including the group body. The request watchdog still
raised `OperationTimeout`, so catching that error outside the group did not make
the waiter's cancellation state clean.

**Fix.** Request waiting uses a watchdog task plus a deadline loop. The watchdog
sleeps and broadcasts the request condition normally; it never raises. The
request coroutine checks the monotonic deadline and aborts the session itself.
The deadline still starts when the request is accepted, including queue time.

**Verification.** The `timeout_reconnect` fault case times out a written publish,
waits for reconnection in the same caller coroutine, and successfully publishes
again. `cancel_reconnect` cancels a written request, observes the old connection
close, then successfully publishes on the next generation.

## D3 — closing a queue failed the write loop

**Problem.** After a session abort, the write loop's dequeue raised once the
queue had been closed, which failed the task group instead of ending the
generation quietly.

**Cause.** `@aqueue.Queue::try_get` raises on a closed queue, but the write loop
treated any raise as a fault.

**Fix.** `Session::next_outgoing` treats a closed queue as normal shutdown and
returns `None`, so the write loop exits cleanly.

**Verification.** `session_wbtest.mbt` covers control-slot reservation, control
exhaustion, FIFO ordering and abort accounting; the integration and demo suites
exercise the real write path.

## D4 — `stats()` reported a discarded backlog

**Problem.** After a disconnect, queue occupancy still reported items that had
been discarded.

**Cause.** `Session::abort` closed both queues with `clear=true`, but the
occupancy mirrors were only decremented on successful dequeue.

**Fix.** `abort` zeroes both mirrors, and `session_wbtest.mbt` asserts it.

**Verification.** Unit test `abort zeroes the queue occupancy mirrors`; the
integration test `test_stats_snapshot_reports_live_connection` asserts a zero
control depth on a live connection.

## D5 — non-finite speeds and empty command ids were accepted

**Problem.** `1e999` parses to `+inf` in the JSON parser and a NaN compares false
against every bound, so a range-free contract could not reject them by
comparison alone. An empty `command_id` was accepted, so a receiver could not
correlate the command. Non-finite telemetry was emitted as the bare tokens
`Infinity`/`NaN`, which are not JSON.

**Fix.** `finite()` uses the self-comparison NaN test (`value != value`) plus
infinity comparisons; `parse_velocity_command` rejects empty ids and non-finite
values; `json_encode_number` emits `null` for non-finite values and all emitted
payloads go through one escaping helper.

**Verification.** `ros_bridge_wbtest.mbt` covers `1e999`, `-1e999`, `NaN`, empty
and missing ids, finite edge values, quotes, backslashes, and C0 control
characters.

## D6 — the thermostat assumed `relay_on = false`

**Problem.** The old controller sample started from `relay_on = false` and
therefore reported a successful OFF for a 27 C sample even though the real relay
state was unknown.

**Fix.** `thermostat_step` takes `Bool?` and returns `Uncertain` inside the
deadband when the state is unknown; the controller queries the device instead of
guessing. `temperature_wbtest.mbt` covers the exact regression.

## D7 — the integration harness required a pty

**Problem.** `tests/integration/run.py` allocated a pty to defeat `println`
block buffering. Sandboxes that deny `/dev/ptmx` could not run any integration
test (`OSError: out of pty devices`), and the buffering hidden by a pty is itself
a defect.

**Fix.** The drivers and demo processes write each JSON line through the async
library's unbuffered stdout writer, and the harness uses a plain pipe.

**Verification.** All 10 integration methods pass without a pty.

## D8 — a slow fragmented packet lost its header

**Problem.** If a peer sent the first byte of a SUBACK and delayed the remaining
bytes for more than 100 ms, the read slice was cancelled after consuming that
byte. The next slice interpreted Remaining Length as a new fixed header and the
operation ended as `OutcomeUnknown` even though the complete SUBACK arrived
inside its 250 ms budget.

**Cause.** The 100 ms timeout wrapped `read_packet`, whose parser state was local
to the cancelled coroutine and could not be reconstructed from the socket.

**Fix.** Only a single-byte read is cancellable. Frame header, Remaining Length,
and body state stay in the read-loop coroutine across any number of idle slices.

**Verification.** Protocol fault cases delay after the fixed header by 150 ms,
delay twice inside the body by 110 ms, and keep an otherwise healthy connection
idle across three read slices before sending a valid SUBACK.

## Advisories left in place

- The read loop checks session shutdown between bounded one-byte reads, so an
  idle connection can take up to 100 ms to observe an abort.
- Clean sessions mean an offline client loses messages; the library still does
  not promise durable delivery.
- The distribution checks (Mooncakes install, public asset checksums, EMQX
  interop, 30-minute soak) are release gates documented in `RELEASE.md`; they are
  not claimed as executed by this document.
