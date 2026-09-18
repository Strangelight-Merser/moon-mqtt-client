# Tasks

## C1 — Preserve terminal disconnect causes (done)

- WHY: API-CONTRACT makes protocol failures/backpressure terminal, but several read-loop branches abort with strings and return; supervisor then observes SessionLost and may reconnect.
- Owner: Sol requested `gpt-5.6-sol`, high; root owns semantic review/integration. Same workspace, HEAD `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb` plus completed J1 patch `513fc3ebdd90d3b32cfa994add708ecc12ce4e85de5daf57df47c3474faf5626`. Full tracked working diff snapshotted `_build/exec-causes/baseline.patch`.
- Sole write ownership: runtime.mbt, session.mbt, client.mbt (preserve J1), NEW focused tests/driver files as needed; no edits to original tests, generated API or original dirty files. No global formatter. Root alone edits exec state/contract after review.
- Required: reproduce a post-CONNACK protocol violation yielding unintended reconnect; preserve typed terminal cause across task shutdown without propagating cancellation into supervisor. Preserve NotSent/OutcomeUnknown, ordinary network reconnect, operation-timeout/caller-cancel recovery, bounded queues and public API.
- Acceptance: regression fails on original code and passes on fix, terminal protocol/backpressure no reconnect and typed error reaches waiters; retryable disconnection still reconnects; native tests plus original full local check. New tests must be runnable in normal checks where feasible without changing original acceptance programs.
- Escalate contract conflict or unsafe lifecycle coupling; otherwise implement. No recursive agents, commit, push, release or hardware.
- Completed: first concrete error retained, direct read/framing and heartbeat errors recorded before pending cleanup, WriteTimeout distinguished from raw I/O, cancellation/retry behavior preserved. Root fixed stale-heartbeat-after-abort race after independent review. C3 verifies final source; public API unchanged.

## C2 — Read-only cause/contract map (done)

- Owner: Luna requested `gpt-5.6-luna`, medium. Same workspace/baseline; no source edits/builds.
- Map abort sources to declared contract and existing tests; identify exact caller cancellation, heartbeat, writer error and control saturation paths. Return evidence and narrow missing coverage, not architecture decisions.
- Dispatch: a new Luna spawn was rejected by thread limit; reused the existing explicitly requested Luna `/root/baseline` via follow-up. Actual model binding metadata remains unavailable.
- Evidence: `_build/exec-causes/luna-cause-map.md`. Root clarified that framing/decode (`read_session_packet`) is outside the packet-handling catch and propagates directly; only handling errors/explicit abort branches are stringified there. This distinction feeds C1 teardown precedence review. Existing cancellation/timeout reconnect tests must remain green; wrong-PUBACK test currently only asserts no false publish success, not terminal waiter type.

## C3 — Integrated local candidate verification (done)

- Owner: root. Depends on C1 semantic review. Run original `MOONBIT_ASYNC_CHECK_FD_LEAK=1 ./scripts/check.sh`, then current unchanged `tests/emqx_interop.py` with available pinned ARM64 broker image. No parallel tests using shared build paths or broker resources.
- Verify 51 protected file fingerprints from `_build/exec-causes/protected.sha256.json`, J1 preservation and generated public API stability. Record new patch/source/log fingerprints. Existing dirty EMQX fixture will be tested as present, not silently committed/adopted or edited. Local macOS client + Linux broker evidence is distinct from a Linux client/hosted CI run.
- Final source passed `scripts/check.sh` with FD checking: native 54/54, Mosquitto 25/25, faults 10/10, scenario and separate TCP/mTLS consumers. Final unchanged EMQX suite 4/4 passed; no running test containers remained. Public API regenerated unchanged; all 51 protected files retained hashes. Logs `accepted-check.log`, `accepted-emqx.log`, `accepted-api.log` under `_build/exec-causes`; final source fingerprints `accepted-source.sha256.json`.

## C4 — Independent lifecycle review (done)

- Owner: `/root/jitter`, retained explicit Sol/high session, independent of C1 implementer `/root/causes`; actual runtime model metadata unconfirmed. Read-only, no builds/source edits.
- Review latest actual runtime/session diff and new terminal-cause tests: restoration-induced OutcomeUnknown must not mask terminal causes; generic teardown must not replace direct protocol/heartbeat errors; cancellation and pending classification unchanged; original transport reconnect preserved. Root integrates findings and final checks.
- Independent review: main type/retry logic correct. P2 direct framing/heartbeat errors still gave pending requests generic cleanup reasons, despite correct supervisor type. Root chose to fix pending diagnostic fidelity within C1; Sol follow-up active. Writer raw-cause test proves storage, not full writer-to-reconnect path; test name/claims must stay narrow. No original tests may change.
- Follow-up: Sol recorded read/framing and heartbeat failures before cleanup, added pending-reason tests, narrowed writer test name and propagated finite peer-handler assertion failures. Independent reviewer found a stale heartbeat timeout after caller abort could replace the synthetic cause. Root added a Cond-synchronized regression: before guard exit 2 (1 failed), after `!self.alive` guard exit 0 (1 passed). Evidence `_build/exec-causes/heartbeat-race-before.log` and `heartbeat-race-after.log`. Root reviewed this minimal guard; final integrated revalidation follows.

## P1 — Local package hygiene (done)

- Owner: root. Local `moon package --list` exposed Python `__pycache__/*.pyc` in archive despite Git ignoring them. Add only cache exclusion patterns to `.moonignore`, repackage and inspect actual ZIP. No publication, dependency changes or original file deletion. Root already ran `moon info . --target native`; generated public API is unchanged.
- Actual ZIP inspected after exclusion: zero Python cache entries, new native tests included. Final archive and checksum recorded by `_build/exec-causes/acceptance.json`; no registry-install claim.

## R1 — Hosted CI handoff (in_progress; authorized)

- Proposed action: commit/push reviewed J1+C1+package/contract changes plus the specifically disclosed preexisting EMQX fixture/doc patch to PR #2; run hosted CI only. Do not merge, tag, publish or operate hardware.
- Review artifacts: `_build/exec-causes/implementation.patch` (SHA256 `11d6a5975a2b449d17c3f066145748ab023faa2c624c04b919f3e6292514f81c`) and `_build/exec-jitter/preexisting.patch`. Existing user prompt explicitly prohibits remote push without authorization; continued local autonomy does not silently lift that boundary.
- User subsequently explicitly authorized this proposed commit/push, including the existing EMQX changes, and delegated routine reversible follow-through. Do not ask again for the same operation. No merge or release authorized. Pre-commit source/protected hashes rechecked against accepted manifests.

## J1 — Default reconnect jitter (done)

- WHY: default clients currently share one deterministic schedule despite the contract's dispersion claim.
- Owner: Sol; requested `gpt-5.6-sol`, high. Workspace/base: current shared workspace, HEAD `1dd505dc45d01ad1c69f5afad33aa2d19c3a9afb`.
- Read: bundle P0/reconnect jitter; `client.mbt` constructors and seed setter, `runtime.mbt` retry delay, `docs/API-CONTRACT.md` reconnection, installed core randomness APIs.
- Sole write ownership: `client.mbt`, `moon.pkg`, a NEW `reconnect_wbtest.mbt`, reconnection paragraph in `docs/API-CONTRACT.md`, relevant comments in `runtime.mbt` only if needed. Do not modify existing tests or other files. Root owns exec state.
- Preserve API, NotSent/OutcomeUnknown, cancellation, retry budget and delay bounds. Prefer existing native/core entropy support; no new external dependency/framework.
- Acceptance: varying default production entropy, deterministic explicit seed, nonzero xorshift, bounded delay; regression catches prior initialization bug; native check/tests/build.
- Deliver: diff, commands/results, entropy-source justification and limits. Escalate if public API/dependency changes or unavailable native entropy require scope change.
- Implemented: native `env.rand(4)` seed, time/counter on entropy absence; original explicit xorshift initializer retained. Only `client.mbt`, `moon.pkg`, new `reconnect_wbtest.mbt`, `docs/API-CONTRACT.md` remain changed for J1. No external dependency/version change.
- Sol check/test/build passed (45/45 native tests); logs `_build/exec-jitter/sol-{check,test,build}.log`. Formatter briefly changed runtime/session/original test formatting; restored byte-for-byte before final validation. Actual dispatched model metadata was not returned.

## B1 — Current baseline (done)

- Owner: Luna; requested `gpt-5.6-luna`, medium. Same workspace/base; read-only, no agents.
- Questions: PR #2/head/checks/releases now; exact fixed toolchain and check commands; CI failure evidence versus dirty local files.
- Deliver: commands, evidence links and limitations; do not infer local patch passes from historical CI.
- Evidence: `_build/exec-jitter/pr-2.json`, `pr-2-checks.txt`, `pr-2-failed-run-35114565663-job-104856572619.log` (all in the same evidence directory). Commands: `gh pr view 2 --json number,title,state,headRefName,headRefOid,baseRefName,url,statusCheckRollup,mergeStateStatus,isDraft,mergedAt,closedAt`; `gh pr checks 2`; `gh run view 35114565663 --job 104856572619 --log-failed`. PR OPEN, base head unchanged, Ubuntu failed/macOS passed. No current-patch CI claim.
- Installed runtime `env.c:230-256` uses `getentropy` for macOS/Linux, returns -1 on failure; core `env.rand` returns None. Actual model metadata unconfirmed (tool returned only task name). No local tests run by Luna.

## V1 — Semantic review and integrated acceptance (done)

- Owner: root/Astra role. Depends on J1; no actual model metadata returned.
- Review entropy behavior across clients/processes, failure semantics and deterministic controls against actual diff. Run required checks once after integration, verify protected hashes, record patch and log fingerprints; update state and tasks.
- Root reviewed actual diff: default constructor is wired into `with_client`; explicit setter/PRNG/delay policy unchanged; four-byte source native runtime is verified; counter has no async suspension; unavailable entropy adds no public error/process termination. 32-bit collisions and weaker cross-process fallback are documented. No protocol-state or public-interface change.
- Protected original tests (16), both preexisting dirty files, runtime/session, generated API, module dependency versions and workspace manifest verified unchanged after formatter cleanup.
- First integrated check passed before formatting restoration. Final `MOONBIT_ASYNC_CHECK_FD_LEAK=1 ./scripts/check.sh` runs after restoration, log `_build/exec-jitter/final-check.log`; only the final result will establish acceptance.
- Patch `_build/exec-jitter/jitter.patch` SHA256 `513fc3ebdd90d3b32cfa994add708ecc12ce4e85de5daf57df47c3474faf5626`; includes the 4 J1 files, excludes preexisting changes and exec state. Source fingerprints: `_build/exec-jitter/source.sha256.json`.
- Final integrated command exited 0 after restoration: native 45/45, Mosquitto 25/25, protocol faults 10/10, scenario and separate local-workspace TCP/mTLS consumers passed. Root rechecked source fingerprints and original-file hashes, and `git diff --check` passed. Machine-readable evidence: `_build/exec-jitter/acceptance.json`. Linux/EMQX/registry/hardware/soak not executed; no release claim.
