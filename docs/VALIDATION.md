# Executed validation — v0.2.0

Platform: macOS arm64. MoonBit: `moon 0.1.20260904 (94521db 2026-09-04)`.
Independent peers: Mosquitto 2.0.22 and Eclipse Paho Python 2.1.0; Python
3.13.12; OpenSSL 3.6.2 (temporary certificates are generated per TLS test).
See `docs/FINDINGS.md` for the defects this round fixed.

## Results

| Check | Command | Observed result |
|---|---|---|
| Type check | `moon check --target native` | Passed, no warnings. |
| Native build | `moon build --target native` | Passed. |
| MoonBit unit tests | `moon test --target native` | 28 passed, 0 failed. |
| Mosquitto/Paho integration | `tests/integration/run.py` | 11 methods passed. |
| Protocol fault injector | `tests/protocol_faults.py` | 5 cases passed. |
| Scenario fixture runner | `moon run examples/scenario_runner` | Passed: Frigate dedup + ROS command/telemetry. |
| State-sync demo smoke | `tests/scenario_smoke.py` | Passed: startup query, ON at 28 C, invalid input ignored, OFF at 26 C, correlated device feedback. |
| State-sync demo scenarios | `examples/mqtt_demo/demo.py` | 4 scenarios passed: `normal`, `lost_puback`, `broker_restart`, `controller_restart`. |
| Separate consumer module | `tests/consumer_smoke.py` | Passed from a fresh module and workspace against the local source copy. |
| All-in-one local check | `./scripts/check.sh` | Passed end to end. |

`moon check` is now warning-free; the previous `fragile_catch_all` advisory is
gone because the supervisor records the failure on the session instead of
re-raising it from a catch-all cleanup block.

## What the new checks establish

**Regression (28 MoonBit tests).** Stream length validation and exact frame
consumption, binary QoS 1 payloads, `NotSent` vs `OutcomeUnknown` classification,
packet-identifier wrap/occupation, inflight bounds, reserved control-slot
independence, control-queue exhaustion, FIFO ordering, occupancy reset on abort,
invalid Will topics, and the contract rules: threshold inclusivity, the
`Uncertain` result for an unknown state in the deadband, non-finite and
empty-id rejection, JSON escaping (quote, backslash, C0 controls), and Frigate
lifecycle/label/zone/dedup behaviour.

**Integration (11 methods, independent Paho/Mosquitto).** TCP QoS 0/1 both
directions and SUBACK results; custom-CA TLS success plus wrong-CA and
wrong-hostname rejection; broker restart with a new generation and
resubscription; bytewise packet fragmentation; lost PUBACK without a false
success; missing PINGRESP causing a disconnect; graceful DISCONNECT suppressing
the Will while an abrupt callback failure sends it; retained delivery and
zero-byte clear observed by a fresh subscriber; unsubscribe followed by a quiet
window; and a `stats()` snapshot on a live connection.

**Fault injector (5 cases, scripted byte-level peer).** Wrong PUBACK identifier;
operation timeout closing before a late ACK and reconnecting with a reused
identifier; DISCONNECT emitted with a full inflight table; partial SUBACK
preserved as one grant and one rejection; a slow consumer terminating with
`Backpressure` under a flood.

**State-sync demo (4 scenarios, separate processes).** The controller never
reports success before correlated device feedback; a lost PUBACK yields
`unknown` followed by a re-query that re-aligns to ON; a broker restart
re-queries and ignores stale retained feedback; a controller restart while the
device stays ON learns ON from its own query rather than from the retained
payload. All four run from one command and assert on both process output and
independent Paho observations.

## Throughput and latency

Not yet measured for a fixed workload. This round establishes the correctness
baseline only; no performance claim is made, and the 30-minute, 1 KiB, QoS 1,
concurrency-16 soak with 100 disconnect/recovery cycles is listed as an open
release gate in `RELEASE.md`.

## Reproduce

```sh
./scripts/check.sh                    # check, test, build, all harnesses
.venv/bin/python examples/mqtt_demo/demo.py   # the four demo scenarios
```

Tool selection can be overridden with `MOON`, `PYTHON`, and `MOSQUITTO`. The
ignored `.tools` and `.venv` installations used here are not part of Git or the
source distribution.

## Limits

This is not a conformance certification, a throughput benchmark or a long-term
soak test. It does not establish EMQX interoperability, authenticated broker
policy, system-root TLS against a public CA, actual hardware execution,
unbounded uptime or leak-free behaviour under sustained load. The device in the
demo is simulated. Hosted CI for this commit is recorded in the repository's
Actions page; a workflow definition alone is not evidence of a completed run.
