# Second-round implementation and evidence ledger

This ledger continues the frozen `0bf9e31bb53964682770af32ab9261468a106c43`
candidate. The original source, package and operator hashes are in the local
`_build/r2/input/manifest.json`. The old candidate and PR #6 remain intact.
The copied user plan is `_build/r2/input/second-round-plan.txt`; it is not a
new acceptance result. This branch retains module version `0.7.1`.

| Work package | Implementation and local acceptance | Remaining boundary |
|---|---|---|
| WP0 | Original source tar preserves Git modes and symlink; old hashes recorded. | Final frozen candidate identity and full cross-platform evidence. |
| WP1 | `disconnects` contract corrected; blocking collector drains after shutdown. Native numeric-CONNACK counter cases and macOS TCP/mTLS short soaks pass. Fault, client and broker-notice ledgers saved. | Broker notices do not capture raw CONNACK/TLS stage per dial; old 100 ms collector A/B and two-platform long soaks remain. |
| WP2 | Original first failure and old/new 100-trial negative results preserved. Isolated post-write race red/green: 60/60 Unknown before the private fix, 60/60 local success after it; held-writer opposite control stays Unknown 60/60. | Three other barrier classes and pinned Linux EMQX test remain; Release HOLD. |
| WP3 | Overflow bucket, exact maximum and split subscribe clocks implemented. Held ACK at 100/160/500 ms and diagnostic trace A/B pass. | Final 13 × 3 × two-platform matrix and variance decision remain. |
| WP4 | SHA/layout-verified, read-only LLDB snapshots pass 1/16 worker calibration, 20 scope lifetimes, 20 established reconnects, 20 native publish timeouts, 20 durable reopens and 20 corrupt-store open failures on local macOS. | Linux SIGSTOP sampler remains. This counts active registered coroutines only; final-binary rerun is required. |
| WP5 | Three injected recovery I/O windows pass local Mac; Core and Integration definitions, separate CI contexts, branch-rules proposal, symlink-aware inventory and E06 gate prepared. Operator `0.1.0` has its own version and format support manifest; preflight clean library 34/34 and operator 9/9 pass. | Frozen package/consumer, Linux CI and Release evidence remain; registry validation waits for actual publication. |
| WP6 | v0.9 retry ADR, format migration RFC and Frigate read-only consumer research delivered. | Future implementation and actual third-party adoption are separate. |
| WP7 | B2 classifier synthetic tests pass. Source hashes for GPIO-disabled firmware match the prior compiled binary (`cff0a390…`), with ESP32 3.3.12, ArduinoMqttClient 0.1.8, ArduinoJson 7.4.3 and documented ESP32-S3 FQBN. | Arduino CLI is unavailable in this shell for a fresh build; no stable field network, pin/wiring review or physical B2-A/B2-B evidence. No device/network operation was made. |
| WP8 | Draft candidate work in progress. Local Core and Integration pass before freeze. | Frozen package, full platform runs, long soaks, benchmark matrix, review packet. No merge, tag, registry upload or release. |

The first-round A–G ledger remains in `../pro-audit/README.md`; its completion
claims are historical and are not automatically transferred to this candidate.
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
