# Native measurement baseline

Run `tests/benchmark.py` for the fixed small matrix: three QoS0 payloads
(32/1024/65000 bytes with a 65536-byte packet bound); QoS1 inflight/concurrency
1/1, 1/16, 32/1 and 32/16; ordinary recoverable and durable delivery stages;
100/1000 restored filters; and Receive Maximum 1/32 with subscription latency.
Defaults are 5 seconds warmup, 30 seconds measurement and 3 independent process
repeats for each workload. This is 39 trials, not a parameter Cartesian product.
`--only` or shorter times produce diagnostic results, never the full baseline.

The benchmark launches a copied native debug executable, records its hash and
actual PID, and samples RSS, numeric file descriptors and OS threads while it
runs. Before-scope, warm and after-scope barriers leave the process alive for
measurement. OS threads are not a census of internal async tasks; workload
workers and pending/queue counts complement them without new public fields.
A local Python raw peer provides an independent wire oracle. Its scheduling,
loopback and trace costs are part of this baseline, so these numbers are not a
claim about maximum client throughput or a ranking against other clients.

Every publication has a phase/worker/sequence measurement ID. Stable workload
outcomes are checked against contiguous raw-wire IDs; any rejected or unknown
operation fails that stable workload rather than being counted as delivered.
QoS0 completion is socket-write completion, not an acknowledgement. The latency
histogram uses 10-microsecond buckets and separately labels observations above
100 ms as censored. Restoration and non-PUBLISH timings retain exact samples.

Ordinary stage measurement uses the existing non-durable recoverable handle so
admission is directly observable; ordinary `publish` throughput is measured in
the QoS1 workloads. Durable admission-return and handle-completion timestamps
are separate from SQLite admission COMMIT and DELETE COMMIT. The test
executable alone registers `sqlite3_auto_extension` / `sqlite3_trace_v2`; it
traces the same statically linked pinned SQLite implementation without modifying
the library/dependency or transaction order. `SQLITE_TRACE_PROFILE` runs after a
statement completes. Only synthetic measurement IDs/stages/timestamps are logged.
SQL/payloads are not exported. Trace/stdout I/O adds overhead and is reported.
See the [SQLite trace API](https://www.sqlite.org/c3ref/trace_v2.html) and
[automatic extension API](https://www.sqlite.org/c3ref/auto_extension.html).

Native and peer timestamps both use **CLOCK_MONOTONIC**. On macOS Python's
`monotonic_ns()` and C `CLOCK_MONOTONIC` can have different origins; the peer uses
`clock_gettime_ns(CLOCK_MONOTONIC)` explicitly. ACK is bounded by peer send-start
and send-return, not the unobservable instant of client parsing. Duration bounds
preserve that distinction; no negative interval is presented as a real latency.

Final Release runs `tests/soak.py --duration 1800 --cycles 100` independently for
TCP and mTLS. Each worker emits contiguous outcome ranges and explicit
acknowledged/unknown/not-sent/rejected counts. The Paho observer validates payloads
and records its own coverage gaps; seeing some messages is not evidence that
all admitted messages were delivered. Samples cover stable connected intervals,
recovery cycles, three warmup scope closures and the final closure before exit.
Cold-to-warm lazy runtime initialization is reported separately.

No new performance threshold, stable-window budget reset, queue enlargement,
weaker persistence or scheduling policy follows automatically from these data.
Pro decides thresholds and tradeoffs after reviewing the baseline and its limits.
