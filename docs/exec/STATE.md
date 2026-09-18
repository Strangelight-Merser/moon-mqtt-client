# Execution state

Updated: 2026-09-18. Status: done for J1, C1, package hygiene and R1 hosted CI.

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

Next release gate requires separately authorized merge/publication and clean registry installation of the actual published modules. No merge, tag, registry/GitHub release, new long soak or ESP32/HA hardware acceptance was performed. Existing TLS timeout/CustomCA classification limitations remain documented in release notes. Do not expand into the next architecture milestone as part of this CI closure.
