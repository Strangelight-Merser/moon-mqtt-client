# Second-round implementation and evidence ledger

> The WP0–WP8 table below is the implementation ledger written before the
> frozen `c52ba9b` Release run. The current review disposition and later
> evidence are in the 2026-09-23 section at the end of this file. Historical
> failures and measurements remain attached to their original commit.

This ledger continues the frozen `0bf9e31bb53964682770af32ab9261468a106c43`
candidate. The original source, package and operator hashes are in the local
`_build/r2/input/manifest.json`. The old candidate and PR #6 remain intact.
The copied user plan is `_build/r2/input/second-round-plan.txt`; it is not a
new acceptance result. This branch retains module version `0.7.1`.

| Work package | Implementation and local acceptance | Remaining boundary |
|---|---|---|
| WP0 | Original source tar preserves Git modes and symlink; old hashes recorded. | Final frozen candidate identity and full cross-platform evidence. |
| WP1 | `disconnects` contract corrected; blocking collector drains after shutdown. Native numeric-CONNACK counter cases, fault/client/broker ledgers and both 100-cycle, 30-minute TCP/mTLS soaks passed on local macOS and hosted Linux at `975e35a`. An isolated 30-second, five-fault old/new collector control consumed 5/5 Disconnected events in each run; see `COLLECTOR-AB.md`. | Broker notices do not capture raw CONNACK/TLS stage per dial; the short control did not reproduce event loss and does not replace final-source soaks. |
| WP2 | Original first failure and negative trials preserved. Isolated post-write race red/green: 60/60 Unknown before the private fix, 60/60 local success after it; held-writer opposite control stays Unknown 60/60. Later local pre-admission, partial-frame and cancellation/deadline probes are recorded in `WSS-INVESTIGATION.md`; pinned Linux EMQX WS/WSS passed at `975e35a`. | Local barrier outcomes need independent review; the initial Linux failure's exact cause remains unproven; final-source Release remains. Release HOLD. |
| WP3 | Overflow bucket, exact maximum and split subscribe clocks implemented. Held ACK at 100/160/500 ms and diagnostic trace A/B pass. The 13 × 3 benchmark matrix passed on local macOS and hosted Linux at `975e35a`; `BENCHMARK-VARIANCE.md` records platform-local variance and trigger results. | Rerun against the final frozen source; no cross-platform ranking or external SLO claim. |
| WP4 | SHA/layout-verified, read-only LLDB snapshots pass 1/16 worker calibration, 20 scope lifetimes, 20 established reconnects, 20 native publish timeouts, 20 durable reopens and 20 corrupt-store open failures on local macOS. Linux ELF x86_64 layout was independently inspected; hosted Integration at `8850b58` measured baseline 1, one-worker open/closed 3/1 and sixteen-worker open/closed 18/1 using SIGSTOP and read-only `/proc/pid/mem`. | Linux 20-cycle groups and final-binary rerun remain. This counts active registered coroutines only. |
| WP5 | Three injected recovery I/O windows pass local Mac; Core and Integration definitions, separate CI contexts, branch-rules proposal, symlink-aware inventory and E06 gate prepared. Operator `0.1.0` has its own version and format support manifest; preflight clean library 34/34 and operator 9/9 pass. Linux Release at `975e35a` exposed a missing `fdatasync` test hook; `132d302` adds it, and hosted Integration passed on Linux/macOS with an exact `fdatasync EIO` marker on `old.sqlite3-journal`. | Rerun exact-source Release and isolated consumers. Registry validation waits for actual publication. |
| WP6 | v0.9 retry ADR, format migration RFC and Frigate read-only consumer research delivered. | Future implementation and actual third-party adoption are separate. |
| WP7 | B2 classifier synthetic tests pass. Source hashes for GPIO-disabled firmware match the prior compiled binary (`cff0a390…`), with ESP32 3.3.12, ArduinoMqttClient 0.1.8, ArduinoJson 7.4.3 and documented ESP32-S3 FQBN. | Arduino CLI is unavailable in this shell for a fresh build; no stable field network, pin/wiring review or physical B2-A/B2-B evidence. No device/network operation was made. |
| WP8 | Draft candidate work in progress. The `975e35a` local macOS Release passed 32 scenarios with two platform N/A; hosted macOS Release passed. Linux Release passed the matrix, soaks, pinned EMQX and package consumers but failed its recovery I/O window, which also prevented evidence-integrity. | Freeze the corrected source and rerun Release on both platforms; complete task census/review packet. No merge, tag, registry upload or release. |

The first-round A–G ledger remains in `../pro-audit/README.md`; its completion
claims are historical and are not automatically transferred to this candidate.

Linux [Release run 35807645417](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35807645417)
failed before the `source_retirement` fault could fire: its first attempt waited
for a CONNACK from the intentionally unstarted peer. The preserved artifact has
no marker for that window. Python's Linux SQLite path used `fdatasync` for
`old.sqlite3-journal`, which the test-only hook did not intercept. After
`132d302`, [Integration run 35817135079](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35817135079)
passed on Linux and macOS; the Linux artifact records `fdatasync EIO` on that
exact journal and three completed recovery windows. This repairs the evidence
fixture, not the library's persistence code. The previous Release failure stays
failed and a new exact-source Release is still required.
The pasted plan alone named R2-01 through R2-06 without defining their individual
texts. The later Pro review supplied those definitions; the 2026-09-23 table
below maps each finding. The visible WP0–WP8 requirements are mapped above.
The selected retry policy and research direction supersede the old
"waiting for Pro" choice rows P01–P03, while WSS findings and release support
claims still require review.

## Review and release boundary

This round prepares a draft candidate. A green inventory confirms its listed
files and checks, not a release verdict. WSS, independent per-dial soak stages,
two-platform final evidence and physical B2 are separate findings. Publication
still requires candidate approval, an authorized upload, fresh registry-only
consumers, and release asset verification.

## 2026-09-23 Pro review and bounded closeout

PR #7 remains Draft at `c52ba9b9f7e8a8aee5ccb2e8b186feeecb6faee6`.
[Release run 35820000248](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35820000248)
bound its Linux, macOS and rolling jobs to that SHA. Linux Release passed
42/42; macOS recorded 31 passes, 10 platform N/A and one E06 failure because
trace A/B was indeterminate; rolling compatibility passed. The combined
Release result is failed. Neither the pre-release extracted-package consumers
nor `MOON_WORK` removal are registry-install acceptance. No merge, tag,
publication or hardware acceptance has occurred.

| Finding | Review disposition | Remaining evidence boundary |
|---|---|---|
| R2-01 | `disconnects` contract and collector event closure accepted; four raw 1800-second, 100-recovery TCP/mTLS ledgers agree with consumed events. | Independent per-dial TCP/TLS/CONNACK stage correlation is absent. `unknown_class=0` is only a client-event time-window classification. |
| R2-02a | Retain the private local-write-completion marker for the identified DISCONNECT settlement race. The follow-up branch adds a real `write_one` regression and a mutation that fails when the marker assignment is changed. | This local-write outcome does not prove broker receipt. |
| R2-02b | Historical first Linux EMQX WSS `OutcomeUnknown` remains unattributed. | The isolated raw barrier packet and forced deadline/cancel supplement are diagnostic builds; they cannot backfill the historical failure. |
| R2-03 | Overflow histogram defect closed, including native 100/160/500 ms delayed-PUBACK counterexamples. | Old censored maxima cannot be reconstructed. |
| R2-04 | Subscribe timing scope fixed. The hosted macOS trace comparison remains indeterminate; the existing off p99 CV is 27.38%. | A separate preregistered six-pair balanced local run finished within its trigger, but uses a local macOS 15 binary/environment and does not change the hosted Release result or establish a PR-to-baseline performance comparison. |
| R2-05 | Read-only active-coroutine method and observed 1→3→1 / 1→18→1 calibration accepted. Follow-up assertions and a reconnect-ready barrier passed branch CI. | This is registered active-coroutine evidence, not all Task objects or a workload peak. |
| R2-06 | Three specified recovery I/O windows closed within their tested scope. | No general filesystem-failure or power-loss claim. |
| Evidence inventory | Original Linux list was internally correct for 425 entries, but omitted 57 nested `result.json` files. | Follow-up inventory includes nested results and is emitted even when a functional gate fails; old artifacts are not rewritten. |

The isolated fixed-cancel WSS diagnostic binary was rerun for only the missing
branches: five of five `WSS_PROBE_DEADLINE` markers and five of five
`WSS_PROBE_SCOPE_RESULT completed=false` markers, each with a complete peer
packet and writer marker before peer close. The peer close was delayed to
400 ms; the branch budget was 100 ms. Raw rows and their hashes remain in the
local review packet. The scope result denotes cancellation of the call, not a
successful MQTT DISCONNECT, and neither batch attributes the historical
Linux failure.

The closeout branch `codex/pro-audit-r2-closeout` began at `c52ba9b` and adds
test and evidence-acceptance code plus this status update. Commit `2641089b` passed local native
tests 158/158, negative oracles for constant census samples, and the
[two-platform Integration run 35868353679](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35868353679).
That selective CI is a regression check for the follow-up changes. It does
not convert PR #7 into a new frozen Release candidate or close the per-dial
stage gap. The v0.8 default retry policy, budget reset, schema and public API
remain unchanged; the v0.9 research direction remains separate.

## 2026-09-25 hosted trace supplement

The preregistered six-pair Mac trace A/B supplement ran on GitHub Actions
macOS 26.6.2 arm64 at diagnostic commit `71b1271` using the unchanged frozen
`c52ba9b` library and benchmark source. [Run 36214415419](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36214415419)
completed all 12 trials and passed ordinary Linux/macOS Integration. The raw
artifact's protocol, single native binary, 12 result files, trace switches and
outcome accounting were independently checked. Throughput population CV was
9.83% off and 10.78% on; p99 CV was **20.83% off and 26.92% on**, both above
the preregistered 15% limit. Its comparison verdict is **indeterminate**.
The workflow's successful execution does not change the original macOS
Release E06 failure or Release HOLD. The local macOS 15.6 `within_trigger`
supplement remains a separate environment and cannot replace hosted evidence.

## 2026-09-25 hosted dial-stage diagnostic

The isolated Mac diagnostic job in [run 36214058165](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36214058165)
succeeded at `91a4eef`, based on frozen `c52ba9b` source with test-only stage
markers and selected broker logging. Two fixed 1800-second, 100-restart runs
passed their soak gates. TCP recorded 135 process-wide attempts: 104 ready
dials and 31 pre-TCP `Connection refused` attempts, with 131 emitted and 131
consumed disconnect events. mTLS recorded 130 attempts: 104 ready dials and
26 pre-TCP refused attempts, with 126/126 disconnect-event closure. All ready
dials matched a source port, broker instance, MQTT identity and numeric
CONNACK 0; mTLS also had 104 TLS-handshake success markers. All 57 failed
attempts began inside recorded broker downtime windows. The raw artifact,
patch and binary hashes, broker notices, outcome counts and event order were
independently checked. This attributes the extra notifications **in these
diagnostic runs**; it does not backfill the old hosted Release artifact or
explain the historical Linux WSS failure.

The overall diagnostic workflow is marked failed because always-on stage
markers in that diagnostic commit disturbed exact-stderr assertions in its
ordinary Integration jobs. A later test-only commit `194a03a` gates those
markers to diagnostic runs; [run 36214594093](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/36214594093)
passed ordinary Linux and macOS Integration. The long-run artifact remains
bound to `91a4eef`, not the later commit. Neither diagnostic commit changes
the frozen PR #7 candidate or converts the failed macOS Release into a pass.
Release HOLD remains.
