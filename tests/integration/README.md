# Integration tests

These tests start an isolated Mosquitto process on an ephemeral loopback port.
Paho is the independent peer/oracle. The packet proxy can fragment broker
packets, delay part of a frame, or terminate a connection before PUBACK reaches
the MoonBit client.

Run all implemented black-box cases with:

```sh
./tests/integration/run.sh
```

Mosquitto and OpenSSL must be on `PATH` (or set `MOSQUITTO` explicitly). Install
the Python oracle with `python3 -m pip install -r tests/integration/requirements.txt`.

The suite currently covers plain TCP QoS 0/1 in both directions, custom-CA TLS
including hostname and trust rejection, broker restart with resubscription, and
a lost-PUBACK request that must never be reported as successful. It also checks
correct and incorrect username/password authentication, an ACL-denied
subscription, fragmented and slow packets, heartbeat failure, Will behavior,
retained delivery and clearing, UNSUBACK followed by an independent no-delivery
window, and the reconnect counters in `Client::stats()`. mTLS cases assert
public `ClientError` variants (`InvalidConfig` vs `TlsFailure` vs `Closed`),
TLS 1.3-only QoS 1, in-process dual identities, and MoonBit certificate ACL
isolation. It is an integration suite, not an MQTT conformance claim.

On failure the temporary broker directory is printed and retained. Successful
runs clean it up.

## Other harnesses

| Harness | What it exercises |
|---|---|
| `tests/protocol_faults.py` | Ten scripted byte-level peers: wrong PUBACK, operation timeout plus reconnect, DISCONNECT with a full inflight table, partial SUBACK, a SUBACK header or body delivered slowly, an idle link then SUBACK, cancellation followed by reconnect, a large incoming PUBLISH, and a slow consumer under a flood. |
| `tests/scenario_smoke.py` | The state-sync demo processes against Mosquitto: startup query, ON at 28 C, invalid input suppressed, OFF at 26 C, correlated device feedback. |
| `tests/consumer_smoke.py` | Builds a fresh consumer module. By default it consumes a local workspace copy; `--registry` installs the published version from Mooncakes. |
| `tests/emqx_interop.py` (`tests/emqx.sh`) | The same core send/receive, subscription-denial and restart-recovery cases against a pinned official EMQX container image. Requires Docker. |
| `tests/soak.py` | Long-running restart soak. Defaults to 30 minutes, 100 disconnect/recovery cycles, 1 KiB QoS 1 payloads and 16 concurrent workers, and gates on an empty final queue plus zero pending requests. |

## Soak evidence and log volume

```sh
MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python tests/soak.py \
  --duration 1800 --cycles 100 --artifacts tests/integration/artifacts/soak
```

The broker log is **quiet by default** (`log_type warning` and `log_type error`)
and is truncated to `--broker-log-limit-mb` (64 MiB) so a long run cannot leave
gigabyte artifacts. Use `--broker-log normal` for connection notices or
`--broker-log debug` for per-packet output when diagnosing a specific failure.

`summary.json` records the driver's final counters, throughput, latency
percentiles, recovery-time distribution, independent observer counts, and the
resource samples. If the platform denies `ps` and `/proc`, the resource section
reports the error and `resource_evidence_available: false` instead of publishing
zeros as measurements.

## Driver protocol

`examples/test_driver` selects a case with `MQTT_TEST_SCENARIO`. It emits one
JSON object per stdout line; build logs and diagnostics belong on stderr.
Required events are documented by the assertions in `run.py`.
