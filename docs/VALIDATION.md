# Executed validation

## Native roadmap candidate — 2026-09-18

Development branch `codex/roadmap-native`, exact tested head `d5e5fb01f33358843fb1e251ad86b03acd687bea` passed [hosted CI35414746155](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35414746155) on macOS and Ubuntu. Both platforms passed native140, Mosquitto/Paho25, protocol faults10, separate-module TCP/mTLS consumers, recoverable QoS1 (5), durable process recovery (4), HA host consumer (4), MQTT5 codec roundtrip, production MQTT5 runtime (12) and WebSocket peers (6). Ubuntu additionally passed EMQX (4) and native WS/WSS (4). Fixed toolchain remains moon0.1.20260904/moonc0.10.12+1634b282e. Latest-toolchain compatibility was not run by this push workflow.

MQTT5 evidence includes independent broker metadata exchange, binary/repeated properties, SIGKILL restart with original packet IDs/DUP and decreasing expiry, empty-outbox session identity, negative ACK persistence, mixed subscription reasons, receive-credit control priority and reduced credit on reconnect. These are host protocol/storage results, not actual ESP32/HA-instance or package-publication claims. The current runtime keeps four nonfatal unused-helper/field warnings; no zero-warning claim is made.

Local complete `scripts/check.sh`, subsequent focused native140 and EMQX WS/WSS checks passed. Exact logs, source/diff fingerprints, model dispatch limits and earlier failures remain in [execution state](exec/STATE.md). The original acceptance assertions and time budgets were preserved; required private fixture initialization and explicitly authorized source-copy/readiness corrections are documented there.

PR#2 was merged externally at2026-09-18T15:02:39Z, and subsequent explicit publication authorization has now been completed: async-tls0.1.0 and MQTT0.3.0 published; unchanged fresh-module registry TCP/mTLS consumer passed. [v0.3.0 release](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.3.0) and all four anonymous asset downloads were verified. The earlier missing-version attempt is historical. The later roadmap branch remains unpublished; actual hardware and its next version/integration remain separate work.

## Historical v0.2.0 validation

Platform: macOS arm64. MoonBit: `moon 0.1.20260904 (94521db 2026-09-04)`,
`moonc v0.10.12+1634b282e` (2026-09-07). Independent peers: Mosquitto 2.0.22,
Eclipse Paho Python 2.1.0, and the pinned official EMQX `5.8.8` container image
(digests in `tests/emqx_interop.py`). Python 3.13.12; OpenSSL 3.6.2 (temporary
certificates per TLS test). See `docs/FINDINGS.md` for the defects this round
fixed.

## Results

| Check | Command | Observed result |
|---|---|---|
| Type check | `moon check --target native` | Passed, no warnings. |
| Native build | `moon build --target native` | Passed. |
| MoonBit unit tests | `moon test --target native` | 26 passed, 0 failed. |
| Mosquitto/Paho integration | `tests/integration/run.py` | 12 methods passed. |
| Protocol fault injector | `tests/protocol_faults.py` | 10 cases passed. |
| EMQX interoperability | `tests/emqx.sh` (pinned 5.8.8 image) | Passed: QoS 1 both directions, subscription denial, restart recovery. |
| Scenario fixture runner | `moon run examples/scenario_runner` | Passed: Frigate dedup + ROS command/telemetry. |
| State-sync demo smoke | `tests/scenario_smoke.py` | Passed: startup query, ON at 28 C, invalid input ignored, OFF at 26 C, correlated device feedback. |
| State-sync demo scenarios | `examples/mqtt_demo/demo.py` | 4 scenarios passed: `normal`, `lost_puback`, `broker_restart`, `controller_restart`. |
| Separate consumer module | `tests/consumer_smoke.py` | Passed from a fresh module and workspace against the local source copy. |
| Registry install acceptance | `tests/consumer_smoke.py --registry` | Passed: a clean temporary module resolved `Strangelight-Merser/moon-mqtt-client@0.2.0` from Mooncakes and completed a QoS 1 round trip. Published package sha256 `ee5af2a2503611a427a8555cdcb3cfaeff5ab4527bd4c85fadfe700998cbb76b` equals the verified local zip; registry record time `2026-09-15T08:35:55Z`. |
| Soak | `tests/soak.py --duration 1800 --cycles 100` | Passed all gates; see the soak section below. |
| Hosted CI | `.github/workflows/check.yml` | Passed on Linux and macOS for `f07e201` after pinning the exact toolchain archives; [PR run](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/34950310413). Merged into `main` at the identical tree `a5ab5d7`. The optional rolling-stable compatibility job is separate. |
| All-in-one local check | `./scripts/check.sh` | Passed end to end. |

`moon check` is now warning-free; the previous `fragile_catch_all` advisory is
gone because the supervisor records the failure on the session instead of
re-raising it from a catch-all cleanup block.

## What the new checks establish

**Regression (26 MoonBit tests).** Stream length validation and exact frame
consumption, binary QoS 1 payloads, `NotSent` vs `OutcomeUnknown` classification,
packet-identifier wrap/occupation, inflight bounds, reserved control-slot
independence, control-queue exhaustion, FIFO ordering, occupancy reset on abort,
invalid Will topics, and the contract rules: threshold inclusivity, the
`Uncertain` result for an unknown state in the deadband, non-finite and
empty-id rejection, JSON escaping (quote, backslash, C0 controls), and Frigate
lifecycle/label/zone/dedup behaviour.

**Integration (12 methods, independent Paho/Mosquitto).** TCP QoS 0/1 both
directions and SUBACK results; custom-CA TLS success plus wrong-CA and
wrong-hostname rejection; broker restart with a new generation and
resubscription; bytewise packet fragmentation; lost PUBACK without a false
success; missing PINGRESP causing a disconnect; graceful DISCONNECT suppressing
the Will while an abrupt callback failure sends it; retained delivery and
zero-byte clear observed by a fresh subscriber; unsubscribe followed by a quiet
window; a `stats()` snapshot on a live connection; and reconnect counters after
a broker restart.

**Fault injector (10 cases, scripted byte-level peer).** Wrong PUBACK identifier;
operation timeout closing before a late ACK and reconnecting with a reused
identifier; DISCONNECT emitted with a full inflight table; partial SUBACK
preserved as one grant and one rejection; a SUBACK whose fixed header arrives
slowly; a SUBACK whose body arrives in slow pieces; an idle link that then sends
a SUBACK; cancellation followed by a successful reconnect; a large incoming
PUBLISH; and a slow consumer terminating with `Backpressure` under a flood. The
slow-frame cases are the regression for bytes lost across idle slices.

**EMQX (pinned official image).** The same core send/receive, subscription-denied
SUBACK and restart-recovery scenarios as the Mosquitto suite, which is what makes
"broker interoperability" a verified statement rather than a Mosquitto-only one.

**Soak (30 minutes, 100 recovery cycles).** Parameters: 1800 s, 100
broker-restart cycles, 1 KiB QoS 1 payloads, 16 concurrent workers,
`MOONBIT_ASYNC_CHECK_FD_LEAK=1`, quiet broker logging. Observed: 10,828,581
acknowledged publishes, 10,405,797 messages seen by the independent Paho
observer with 0 corrupt payloads, 101 generations, 100 reconnects and 100
disconnects. All final gates were met: `pending=0`, `business=0`, `control=0`,
`event_queue=0`, `active_workers=0`, every cycle recovered (100/100) and the
driver exited 0. Recovery time min/median/p95/max = 0.302/0.326/0.351/0.376 s.
The run used 18,646 bytes of driver evidence; the quiet, capped broker log stayed
at 0 bytes.

**State-sync demo (4 scenarios, separate processes).** The controller never
reports success before correlated device feedback; a lost PUBACK yields
`unknown` followed by a re-query that re-aligns to ON; a broker restart
re-queries and ignores stale retained feedback; a controller restart while the
device stays ON learns ON from its own query rather than from the retained
payload. All four run from one command and assert on both process output and
independent Paho observations.

## Throughput and latency (single-machine baseline)

From the soak run above, on this macOS arm64 development machine with both
processes sharing one host: 6,012 acknowledged QoS 1 publishes per second
end-to-end, with p50/p95/p99 acknowledgement latency of 3/3/3 ms and a maximum of
144 ms. Recovery after a broker restart took 0.30-0.38 s.

These are single-host baseline numbers for a debug build, not a performance
claim. They exist so a later change can be compared against something measured.
No production-throughput conclusion is drawn.

The soak's automatic resource sampling could not run in the sandbox used here
(`ps` is denied, so `resource_evidence_available` is `false`), so RSS and
open-file-descriptor curves are **not** part of this evidence. What is covered:
the async runtime's own `MOONBIT_ASYNC_CHECK_FD_LEAK` check during the run, and
a driver that exits 0 after draining with zero pending requests and zero queued
work. A run on a machine that permits `ps` or exposes `/proc` will populate the
resource section automatically.

## Release acceptance recheck (2026-09-15)

The source archive generated from tag `v0.2.0` (`85bc0a5`) was extracted into
a clean temporary directory. `scripts/check.sh` passed there: 26 unit tests,
12 Mosquitto/Paho integration methods, 10 protocol fault cases, the scenario
smoke and the separate consumer module. The four state-sync demo scenarios also
passed. `tests/consumer_smoke.py --registry` then installed `@0.2.0` into a fresh
temporary module and completed the QoS 1 round trip.

The source archive SHA-256 is
`0c0dbfb24328d803695d53f139a25cde017af835c49c24c959f92b95a9ee71f7`.
The original 30-minute soak and EMQX evidence above was retained; those longer
checks were not repeated for the documentation and CI-installation update.

Fresh branch and tag workflows initially stopped before tests because the
old installer downloaded the rolling `latest` archives. The fixed installer now
uses the official `0.10.12+1634b282e` path (without a leading `v`), verifies the
original platform hashes and core SHA-256
`784a12ce4e204a3a98a0b704a021f747b916412efacd4dfe2f4e5c27ae183ac1`, and installs
those verified bytes. A clean macOS installation passed core bundling and the
exact `moon`/`moonc` version checks. Both Linux/macOS push and PR checks then
passed on `f07e201`; PR #1 merged that tree into `main` at `a5ab5d7`.
See D11 in `docs/FINDINGS.md`.

The GitHub Release's package ZIP, source archive and checksum file were then
downloaded from their public URLs without authentication. All three match the
local files byte for byte; the package and source hashes are recorded in
`docs/RELEASE-NOTES-v0.2.0.md`.

Local acceptance and installation logs are under the ignored
`_build/release-v0.2.0/` directory; the public CI and asset records are linked
from `docs/RELEASE-NOTES-v0.2.0.md`.

## Reproduce

```sh
./scripts/check.sh                    # check, test, build, all harnesses
.venv/bin/python examples/mqtt_demo/demo.py   # the four demo scenarios
.venv/bin/python tests/consumer_smoke.py --registry   # published 0.2.0 install
```

Tool selection can be overridden with `MOON`, `PYTHON`, and `MOSQUITTO`. The
ignored `.tools` and `.venv` installations used here are not part of Git or the
source distribution.

## Limits

This is not a conformance certification and the performance numbers are a
single-host baseline, not a benchmark. The soak covers 30 minutes and 100
recoveries, not days of uptime. It does not establish system-root TLS against a
public CA, actual hardware execution or leak-free behaviour under sustained
load beyond the FD-leak check and the drained-queue gate. The device in the demo
is simulated. Hosted CI results above identify `f07e201` and its merge tree;
later documentation commits have their own runs on the repository's Actions page.
