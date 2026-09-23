# Second-round implementation and evidence ledger

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
The pasted plan names R2-01 through R2-06 without defining their individual
texts. They cannot be mapped faithfully until the underlying Pro review or
`CODEX_NEXT_ROUND.md` is available. The visible WP0–WP8 requirements are mapped
above. The selected retry policy and research direction supersede the old
"waiting for Pro" choice rows P01–P03, while WSS findings and release support
claims still require review.

## Review and release boundary

This round prepares a draft candidate. A green inventory confirms its listed
files and checks, not a release verdict. WSS, independent per-dial soak stages,
two-platform final evidence and physical B2 are separate findings. Publication
still requires candidate approval, an authorized upload, fresh registry-only
consumers, and release asset verification.
