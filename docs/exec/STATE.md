# Execution state

Updated: 2026-09-18. Status: W1 in_review with a hosted Linux WSS blocker; expanded roadmap remains in_progress. J1/C1/R2 done.

## Expanded scope and active checkout

User explicitly requested autonomous continuation until the preceding plan is fully executed. This supersedes the earlier one-milestone stopping point. Progress sequentially through WS/WSS, reconnect-resilient QoS 1, one-backend durable outbox, application-driven MQTT 5 and host-side ESP32/HA consumer work. Preserve explicit non-goals and distinguish unavailable hardware/publication acceptance. No new merge/publication/hardware authorization was granted.

Active implementation worktree: `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`, branch `codex/roadmap-native`, base `eeb93be40ba19020df1a9effcdd8771c5f132104` (aligned to the passing R2 commit without changing tested implementation bytes). The original `moon-mqtt-client` checkout and PR #2 stay at the v0.3 candidate. New work must not accidentally expand that PR. Toolchain and Python environment are shared via symlinks; a support task unnecessarily installed `websocket-client==1.9.2` into the shared Python environment while diagnosing broker readiness. Paho 2.1.0 already implements WebSocket itself; root subsequently uninstalled exactly that newly introduced package. No dependency declaration was added; final integration runs use only the existing Paho requirement. Builds and fixture resources remain isolated. Bundle reference copy is under `_build/research/moon_mqtt_codex_bundle`.

W1 native WS/WSS is implemented and locally accepted. Public API adds NetworkTransport and Config.transport; existing publish API/completion semantics stay. CLI --ws-path reuses TLS/mTLS flags. The small provenance-tracked frame engine requires mqtt subprotocol, binary streaming, bounded buffering, secure masking and explicit close/error behavior. Root inspected final API and semantics, independently tested raw peer and EMQX, and verified source/provenance fingerprints.

Final local evidence `_build/exec-ws/integrated-check.log`: native71, original broker25, faults10, scenario and external local-workspace TCP/mTLS consumers passed with FD checking. `integrated-protocol.log`:6 test cases (including multiple invalid handshake/frame variants) passed. `integrated-interop.log`:4 real native CLI WS/WSS tests passed, including QoS0/1 both directions and mTLS rejection cases; strict Python ResourceWarning checks clean, no unnecessary websocket-client dependency. Local package includes internal source/licenses and excludes Python caches/tool environments. This is not registry installation.

Earlier failures are retained: external-consumer copier omitted new source packages; normal-scenario process-liveness check let a command precede device SUBACK. Root corrected only those fixture setup/copy blocks under routine follow-through authorization, with original application assertions/timeouts unchanged. Tests are not claimed wholly byte-unchanged: those two fixture files and the CLI are the disclosed exceptions; other protected originals remain unchanged. See TASKS for exact wire evidence.

Sol implementation/review were separate sessions; reviewer approved binary protocol changes subject to guarded-writer regression, now passing with a mutation counterexample. Root chose synchronous fail-close for detected malformed frames so cleanup cannot mask protocol errors. W1 commit `799575004fc3624d7875c42b452b49b068fde512` passed hosted macOS but run35359571920 failed Linux WSS subscriber reception after SUBACK; WS and WSS publishing passed. Full failed logs retained `_build/exec-ws/ci-first-failed.log`. This is an unresolved acceptance blocker, not an approved flaky retry. Root is adding stdout/stderr/broker diagnostics and Sol independently investigates. Next planned capability is explicit reconnect-resilient QoS1 delivery, keeping ordinary publish semantics.


## R2 hosted regression resolved

Final v0.3 closure uncovered an intermittent missing-client-certificate classification failure at `0a604f4`. A raw socket-error classification gap was proved independently; exact hosted exception remains unavailable. Sol fixed the encrypted CONNECT/CONNACK boundary, preserving malformed MQTT, plain transport and timeout/cancellation types. Root reviewed and ran full local check (native58, broker25, faults10, scenarios/consumers), protected hashes and API checks. Deterministic injected error failed without the mapping and passed with it.

Fix commit `eeb93be40ba19020df1a9effcdd8771c5f132104` pushed to the original v0.3 PR. [PR CI35356886328](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35356886328) and [push CI35356879535](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35356879535) both passed Ubuntu/macOS, including Ubuntu EMQX. Original-checkout evidence `_build/exec-tls-flake/`. The R2 fix is integrated, and this branch is based directly on that accepted commit.

## Current baseline and authorization

- Workspace: `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`; branch `codex/v0.3-mtls`.
- Initial base: `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb`. Accepted implementation committed and pushed as `d61eb67482f7bc947cf0b1eab3b2931e8ec993ec` to [PR #2](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/2), still OPEN. Hosted checks passed and GitHub reported CLEAN.
- User explicitly approved commit/push of reviewed implementation AND the preexisting EMQX fixture/doc changes, and delegated routine reversible follow-through. Merge, tags, publication, production and hardware operations remain outside authorization.
- This closing documentation update changes no implementation. CI below binds the implementation commit explicitly; future code changes require appropriate fresh evidence.
- Execution bundle: `/Users/huaiyi/Downloads/moon_mqtt_codex_execution_bundle.zip`; strategy and relevant probes read. Historical probes are models, not native acceptance.
- Toolchain: `./scripts/moon.sh`, local `.tools/moon`, moon `0.1.20260904 (94521db 2026-09-04)`; no dependency/toolchain upgrade.

## Delivered behavior and decisions

- J1: default reconnect seeds use native `env.rand(4)` entropy. When unavailable, time plus a process-local advancing counter avoids a universal fixed fallback; cross-process uniqueness is not guaranteed. Explicit seeded xorshift sequence, delay bounds, retry budget and public API remain unchanged. Entropy absence must not abort the process because jitter is not security material.
- C1: terminal protocol/backpressure errors retain their types through read-loop shutdown and subscription restoration. Pending OutcomeUnknown and generic cleanup cannot replace the initiating concrete cause. Existing NotSent/OutcomeUnknown classification and string reasons remain.
- Framing/read and heartbeat causes are stored before settling pending requests. Actual write timeout is distinguished from raw writer I/O. A stale heartbeat cannot overwrite an already-aborted session. Ordinary transport loss, operation timeout and caller cancellation retain recovery behavior.
- Independent review identified pending diagnostic loss and stale heartbeat overwrite; both resolved. Root's synchronized regression failed before the heartbeat guard and passed after it. The raw writer-error test proves stored type, not complete writer-failure reconnection.
- Approved preexisting EMQX changes enable container access to the throwaway server key and clean partially started fixtures. Their source bytes were preserved during implementation and explicitly approved before committing.
- `.moonignore` excludes Python cache artifacts. No public API, protocol scope or dependency version change. No WS/WSS, persistent session or durable outbox expansion.

## Verification and evidence

- Root local integrated `MOONBIT_ASYNC_CHECK_FD_LEAK=1 ./scripts/check.sh`: native 54/54, Mosquitto 25/25, protocol faults 10/10, scenario smoke and independent local-workspace TCP/mTLS consumers passed.
- Separate local `MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python tests/emqx_interop.py`: 4/4 passed; test containers cleaned. Local platform is macOS ARM with a Linux ARM64 broker.
- Hosted [PR run 35354946801](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35354946801) and [push run 35354944159](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35354944159), both at `d61eb67482f7bc947cf0b1eab3b2931e8ec993ec`: Ubuntu and macOS jobs passed, including native 54/54, broker 25/25, fault/scenario and TCP/mTLS consumers. Ubuntu EMQX 4/4 passed. macOS EMQX and latest-compatibility jobs are conditional skips, not passes.
- Luna retrieved hosted job metadata/logs; root independently checked exact head SHA, conclusions and test summaries. `_build/exec-ci/{pr-35354946801,push-35354944159}-final.{json,log}`, `summary.md`, `submission.json` retain local evidence.
- Native API regeneration unchanged; `git diff --check` passed. All 51 protected source/test/example fingerprints matched `_build/exec-causes/protected.sha256.json`. Committed source bytes match `accepted-source.sha256.json`.
- Local logs: `_build/exec-causes/accepted-check.log`, `accepted-emqx.log`, `accepted-api.log`, `heartbeat-race-before.log`, `heartbeat-race-after.log`, `acceptance.json`. Ignored `_build` evidence is local, not committed.
- Pre-commit implementation patch: `_build/exec-causes/implementation.patch`, SHA256 `11d6a5975a2b449d17c3f066145748ab023faa2c624c04b919f3e6292514f81c`, excluding exec state and preexisting fixture/doc patch. The latter is `_build/exec-jitter/preexisting.patch`; both are now represented in the implementation commit.
- Local package snapshot (before closing state updates): `_build/publish/Strangelight-Merser-moon-mqtt-client-0.3.0.zip`, SHA256 `50a860324646752a9b3b18bda9859998bb0881ca084f837ccb995237bceb1fcf`; 80 entries, no Python caches, implementation bytes verified. This is not a published or registry-installed artifact.

## Model dispatch

The collaboration interface explicitly exposes `model`, `reasoning_effort`, and `fork_turns`; supported requested IDs include `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-luna`. Explicit overrides require a non-full-history fork. Root owns scope, semantic review and integration; actual root model metadata is unconfirmed.

- `/root/baseline`: requested `gpt-5.6-luna`, medium, fork none; read-only CI/toolchain evidence. Tool returned task ID, not actual model metadata.
- `/root/jitter`: requested `gpt-5.6-sol`, high, fork none; implemented and ran native check/test/build. Tool returned task ID, not actual model metadata. No recursive delegation. Root independently reviewed actual diff and ran integrated acceptance.
- `/root/causes`: requested `gpt-5.6-sol`, high, fork none; C1 implementation/reproducer. A new Luna spawn hit the environment's thread limit, so C2 reuses `/root/baseline` (previous explicit Luna binding) via follow-up. No actual model metadata returned. Root reviews lifecycle semantics and runs integrated acceptance.
- `/root/jitter` independently reviewed C1 in a separate Sol session from `/root/causes`; it did not implement C1. Root fixed the final stale-heartbeat race and verified before/after and integrated behavior. All requested bindings are explicit; runtime actual-model metadata remains unconfirmed.

## Handoff and remaining boundaries

This milestone is complete: default reconnect dispersion and typed terminal failures are fixed, and the previous hosted Linux/EMQX CI blocker is cleared on the accepted implementation. Resume by reading this file, TASKS.md and actual Git state; do not rerun historical work without a new change or specific risk.

Next release gate requires separately authorized merge/publication and clean registry installation of the actual published modules. No merge, tag, registry/GitHub release, new long soak or ESP32/HA hardware acceptance was performed. Existing TLS timeout/CustomCA classification limitations remain documented in release notes. The later explicit user instruction expands software scope; release and hardware gates remain separate.
