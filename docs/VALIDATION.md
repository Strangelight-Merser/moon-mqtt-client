# Executed validation — 2026-09-14

Platform: macOS arm64. MoonBit: `moon 0.1.20260904`,
`moonc v0.10.12+1634b282e` (2026-09-07).
Independent peers: Mosquitto 2.0.22 and Eclipse Paho Python 2.1.0;
Python 3.13.12. TLS tests generate fresh temporary CA/server certificates.

## Results

| Check | Observed result |
|---|---|
| `moon check --target native` | Passed; one compiler advisory, described below. |
| `moon build --target native` | Passed. |
| MoonBit tests | 13 passed, 0 failed. |
| Independent Mosquitto/Paho integration | 9 test methods passed. |
| Controlled protocol fault injector | 5 cases passed. |
| Scenario network fixtures | Temperature, Frigate events and ROS-side command/telemetry contracts passed. |
| Separate consumer module | Fresh source copy, new module and workspace, actual QoS 1 subscribe/publish/receive passed. This is local-module consumption, not a Mooncakes installation test. |

`validation-logs/final-check.log` contains the final all-in-one run. The test
harness checks external traffic and process outcomes. Protocol-injector socket
timeouts are failures, not evidence that a connection was closed.

## What was exercised

The 13 MoonBit tests cover stream length validation and exact frame consumption,
binary QoS 1 payloads, pending-request outcome classification, packet identifier
wrap/occupation, inflight bounds, invalid Will topics and the three scenario
rules. Several methods contain multiple boundary assertions.

The nine independent-broker tests cover:

- TCP QoS 0/1 in both directions and subscribe acknowledgements;
- custom-CA TLS success, wrong CA rejection and wrong hostname rejection;
- broker restart, new connection generation and resubscription;
- broker-to-client bytewise packet fragmentation;
- lost PUBACK without a false successful publish;
- missing PINGRESP causing a disconnect;
- graceful DISCONNECT suppressing Will, and callback failure causing Will;
- retained publish observed by a fresh subscriber, and empty retained publish clearing it;
- unsubscribe completion followed by a bounded window with no further delivery.

The five protocol-fault cases cover:

1. A PUBACK with the wrong ID cannot complete the publish and the client closes.
2. A live connection without PUBACK reaches the ACK deadline and closes **before**
   the injector sends the late ACK. An immediate `wait_connected()` waits for the
   new connection; its publish completes only after its own ACK, even when ID 1
   is reused on the new connection.
3. With one inflight slot occupied, graceful close still emits actual `E0 00`.
4. SUBACK `[0x00, 0x80]` returns one granted result and one rejection.
5. A consumer which does not drain its two-slot event queue terminates with
   `Backpressure` during a flood and closes the transport.

The finite scenario runner uses the documented Frigate `type`/`after` fields,
deduplicates repeated completed person events, converts an application command
envelope to nested Twist JSON, checks six Twist fields and roundtrips primitive
telemetry/receipts. The separate temperature test observes ON, deadband and
invalid-input suppression, OFF and an offline Will through Paho. These are
fixtures, not a running Frigate service, ROS graph or physical actuator.

## Reproduce

With MoonBit, Mosquitto, OpenSSL and the pinned Paho test dependency available:

```sh
./scripts/check.sh
```

Tool selection can be overridden with `MOON`, `PYTHON`, and `MOSQUITTO`. This
local development checkout also has ignored `.tools` and `.venv` installations,
so the command runs here without relying on the earlier research project.
Those installations are not included in Git or the source distribution.

For only the library tests, use `moon test --target native`. For only external
interoperability, use `./tests/integration/run.sh`. The other scripts are
`tests/protocol_faults.py`, `tests/scenario_smoke.py`, and `tests/consumer_smoke.py`.

## Limits

This is not a conformance certification, a throughput benchmark or a long-term
soak test. Linux/macOS hosted CI is defined but has not executed publicly.
System-root TLS is implemented but the reproducible positive TLS test uses a
custom CA. Username/password fields are encoded but an authenticated broker
policy was not part of these tests. The tests do not establish interoperability
with every broker, actual hardware execution, unbounded uptime or leak-free
behavior under sustained load.

The compiler reports `fragile_catch_all` in the session supervisor where the
original I/O error is recorded and rethrown. An enclosing `defer` independently
closes the session on every exit, including cancellation. This advisory remains
visible; future async-runtime/toolchain updates require rerunning cancellation
and connection-lifecycle tests.
