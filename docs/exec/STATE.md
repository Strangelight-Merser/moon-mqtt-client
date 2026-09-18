# Execution state

Updated: 2026-09-18. Status: in_progress for R1 hosted CI; J1/C1 and local package hygiene done.

## Current authorization and action

The user explicitly approved committing/pushing the reviewed implementation AND the preexisting EMQX fixture/doc patch to PR #2 for CI, and delegated routine reversible decisions. Proceed without repeated confirmation. Merge, tags, registry/GitHub publication and hardware remain outside this authorization. Earlier no-push statements below describe the pre-authorization baseline.

Accepted source/protected-file hashes were rechecked before staging; no code changed after final local verification. R1 will bind the pushed commit and hosted checks here. Local archive/evidence remain snapshots from before this state update.

Current user steering: continue autonomously through ordinary implementation decisions; only serious decisions/vulnerabilities warrant stopping. Existing remote/hardware authorization boundaries remain. Resume from actual dirty tree, not HEAD alone; preserve completed J1.

## Current result: C1 and package hygiene

- Protocol/backpressure failures retain typed causes through read-loop shutdown and subscription restoration. A pending request's OutcomeUnknown cannot mask its initiating ProtocolError. Generic cleanup cannot replace direct errors.
- Framing/read and heartbeat causes are recorded before pending requests settle; existing NotSent/OutcomeUnknown classification and original string reasons remain. WriteTimeout is distinct from raw writer I/O errors. Network loss, request cancellation and operation-timeout recovery still pass original tests.
- Independent Sol review found pending diagnostic loss and a stale heartbeat timeout after caller abort. Both were fixed. Root's synchronized stale-heartbeat test failed before the guard (exit 2, PingResponseTimeout) and passed afterward (exit 0). The writer test explicitly proves stored raw cause, not end-to-end writer-failure reconnection.
- Final root-run `MOONBIT_ASYNC_CHECK_FD_LEAK=1 ./scripts/check.sh`: exit 0, 54 native tests, 25 Mosquitto integration tests, 10 protocol faults, scenario smoke and separate local-workspace TCP/mTLS consumers passed. `MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python tests/emqx_interop.py`: exit 0, 4/4 passed; Docker test containers cleaned. This is macOS client plus pinned Linux ARM64 EMQX broker, NOT Linux-client CI.
- `moon info . --target native` regenerated an unchanged public interface. `git diff --check` passed. All 51 protected tests/examples/preexisting dirty files match `_build/exec-causes/protected.sha256.json`.
- Package check found Python cache files in archives. `.moonignore` now excludes `**/__pycache__/` and `**/*.pyc`; actual local ZIP inspected without cache entries. No original files deleted. Local packaging is not registry installation/publication.
- Current base/HEAD still `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb`, no commit. Cumulative own patch (J1+C1+package hygiene+contract/release notes): `_build/exec-causes/implementation.patch`, SHA256 `11d6a5975a2b449d17c3f066145748ab023faa2c624c04b919f3e6292514f81c`. Excludes original EMQX/doc dirty patch and exec state. Tested source manifest: `_build/exec-causes/accepted-source.sha256.json`.
- Evidence: `_build/exec-causes/accepted-check.log`, `accepted-emqx.log`, `accepted-api.log`, `heartbeat-race-before.log`, `heartbeat-race-after.log`, and final `acceptance.json`. Ignored `_build` evidence is local, not committed. New original-bug regression is native, not a Python state model.
- Remote boundary: PR #2's recorded Linux failure is not cleared by local success. No push/merge/tag/publish, current-patch hosted Linux CI, registry install, long soak or ESP32/HA run. Existing dirty EMQX permissions/cleanup patch was preserved and exercised, not silently included in our implementation patch or committed.
- Next actionable gate: explicit authorization to include the existing EMQX fixture/doc changes together with this reviewed implementation, commit and push to PR #2 for hosted CI. Merge/release remain separate. Avoid WS/WSS or session/persistence expansion before closing this v0.3 gate.

## Baseline and scope

- Workspace: `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`.
- Branch: `codex/v0.3-mtls`; base/HEAD: `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb`.
- Existing dirty files are protected: `tests/emqx_interop.py` (SHA256 `e52d8de0323c21388486a0255f5be886141e2fc80468628e214a3e71b988c49b`) and `docs/V0.3-FIXES-2026-09-16.md` (`b457a28f7e80b2fc6f401df71784bd7229898a9ce679761bee54a1860fad7a1f`). Snapshot: `_build/exec-jitter/preexisting.patch`.
- Execution bundle: `/Users/huaiyi/Downloads/moon_mqtt_codex_execution_bundle.zip`; read in preceding turn, including inner probe source. Probes are models, not native acceptance.
- Toolchain: `./scripts/moon.sh`, local `.tools/moon`; moon `0.1.20260904 (94521db 2026-09-04)`. Plain `moon` is not on PATH. No toolchain/dependency upgrade authorized or needed.
- Milestone: correct default reconnect seed selection while preserving deterministic seed injection, public API, retry budget and delay bounds. No WS/session/persistence expansion.
- Confirmed bug: `with_client` constructs every backoff with seed 0; xorshift maps that to the same constant. Existing contract claims clients avoid lockstep.
- Existing EMQX changes will not be adopted, reverted or edited in this milestone. CI and release readiness are separate from this patch's acceptance.
- No remote push, PR merge, release, production or hardware actions authorized.

## Model dispatch

The collaboration interface explicitly exposes `model`, `reasoning_effort`, and `fork_turns`; supported requested IDs include `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-luna`. Explicit overrides require a non-full-history fork. Root owns scope, semantic review and integration; actual root model metadata is unconfirmed.

- `/root/baseline`: requested `gpt-5.6-luna`, medium, fork none; read-only CI/toolchain evidence. Tool returned task ID, not actual model metadata.
- `/root/jitter`: requested `gpt-5.6-sol`, high, fork none; implemented and ran native check/test/build. Tool returned task ID, not actual model metadata. No recursive delegation. Root independently reviewed actual diff and ran integrated acceptance.
- `/root/causes`: requested `gpt-5.6-sol`, high, fork none; C1 implementation/reproducer. A new Luna spawn hit the environment's thread limit, so C2 reuses `/root/baseline` (previous explicit Luna binding) via follow-up. No actual model metadata returned. Root reviews lifecycle semantics and runs integrated acceptance.
- `/root/jitter` independently reviewed C1 in a separate Sol session from `/root/causes`; it did not implement C1. Root fixed the final stale-heartbeat race and verified before/after and integrated behavior. All requested bindings are explicit; runtime actual-model metadata remains unconfirmed.

## Acceptance and next step

Native focused regression must catch shared default initialization without probabilistic single-pair assertions; explicit same seeds remain reproducible, zero remains safe, jitter remains bounded. Run repository `scripts/check.sh` on integrated tree. Root reviews actual diff and confirms protected-file hashes. Record final patch fingerprint and evidence. Remote Linux/registry/hardware evidence must remain separately marked if unexecuted.

## Final result and handoff

- J1 done: default clients now consume native entropy instead of a universal fixed seed. Explicit seeded PRNG and public API remain unchanged; unavailable entropy uses the documented time/counter policy. No changes to retry timing formula, protocol state, TLS ownership or dependency versions.
- Final root-run command: `MOONBIT_ASYNC_CHECK_FD_LEAK=1 ./scripts/check.sh`, exit 0, after scope cleanup. Native check/build passed; 45 native tests, 25 real Mosquitto integration tests and 10 protocol fault cases passed; scenario smoke plus separate TCP/mTLS consumers passed using a local workspace copy (NOT registry installation).
- Evidence: `_build/exec-jitter/final-check.log`, `acceptance.json`, `source.sha256.json`, `original-tests.sha256.json` in that directory. `git diff --check` passed. All 16 original test files and the two preexisting dirty files retained their baseline hashes; formatter collateral was removed before final acceptance.
- Base and HEAD remain `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb`; no commit created. Reviewable patch `_build/exec-jitter/jitter.patch` SHA256 `513fc3ebdd90d3b32cfa994add708ecc12ce4e85de5daf57df47c3474faf5626` includes J1 source/contract/new tests only. Exec state and original dirty changes are excluded; source manifest binds tested files. Local evidence under `_build` is ignored by Git.
- Not run: current-patch Linux CI, EMQX, registry installation, long soak, ESP32/HA. No push/merge/tag/release/hardware operation performed. Current Linux CI remains red at base; this milestone does not certify v0.3 release readiness.
- Next independent batch: reproduce typed disconnect-cause loss using a malformed/unexpected ACK after a successful connection, then decide terminal versus retry behavior against API-CONTRACT. Preserve original tests and add new focused coverage. Separately resolve ownership/approval of the existing EMQX dirty patch before adopting any fixture change; root startup diagnostics and unique-resource work remain outside J1. Registry install requires separately authorized publication; no remote authorization is needed to use/review this local patch.

## Live baseline findings

- Luna checked PR [#2](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/2): OPEN/UNSTABLE, head equals baseline. PR run [35114565663](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35114565663) and push run [35114561976](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35114561976) have macOS success and Ubuntu failure. PR Ubuntu failure starts with EMQX mTLS listener timeout, then container-name conflict; push run additionally has reconnect/resubscribe failure. These are historical runs of base commit, not verification of today's patch or dirty files.
- GitHub release `v0.3.0` not found; latest GitHub release is `v0.2.0`. Mooncakes registry status was not queried.
- Existing script `scripts/check.sh` covers native check/test/build, Mosquitto integration, protocol faults, scenarios and separate local-workspace consumer. EMQX is a separate Linux CI step, not part of this script.
- Deferred candidate confirmed by source inspection, not a fresh reproducer: `Session.failure_reason` is String and several read-loop protocol failures abort and return; supervisor maps normal reader return to `SessionLost`. Typed terminal-cause preservation deserves its own lifecycle task, outside J1.
- Root found core `Rand::new()` has a fixed-seed fallback when native entropy fails; Sol was asked to account explicitly for entropy-source behavior before selecting implementation.

## J1 decision: entropy failure policy

Root rejected process abort on unavailable entropy: jitter is not security material and must not turn a recoverable environment condition into process termination. Use native `core/env` entropy when available; otherwise combine time with a process-local advancing counter. Document the weaker fallback (cross-process collisions remain possible), with no uniqueness/security claim. Preserve explicit-seed sequence and zero normalization. Private deterministic seams may cover entropy absence and same-time creation without replacing globals in tests. No new public error or dependency. Existing original test file hashes are captured in `_build/exec-jitter/original-tests.sha256.json`.
