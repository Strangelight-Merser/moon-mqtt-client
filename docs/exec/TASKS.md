# Tasks

## Current handoff (2026-09-18)

- done: J1/C1/R2 v0.3 reliability; W1 native WS/WSS; Q1 recoverable QoS1; D1 SQLite durable outbox; H1 host controller/simulator; M1 application-facing MQTT5 runtime.
- Final acceptance: d5e5fb01f33358843fb1e251ad86b03acd687bea / [CI35414746155](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35414746155), both platforms success, native140 and runtime12 plus original suites. Evidence/details and model dispatch limitations in STATE.
- blocked external validation: actual HA/ESP32 requires board/pins/access. v0.3 registry TCP/mTLS now passed after publication; the earlier missing-version attempt remains historical evidence.
- done after explicit authorization: TLS0.1 then MQTT0.3 publication, fresh registry TCP/mTLS, v0.3 tag/release and four anonymous asset checks. Separate follow-up: roadmap version/integration decision. PR#2 was already merged by repository owner.
- Historical task cards below retain their original stage labels; this handoff and STATE are authoritative for current completion.

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
- Independent review: main type/retry logic correct. P2 direct framing/heartbeat errors still gave pending requests generic cleanup reasons, despite correct supervisor type. Root chose to fix pending diagnostic fidelity within C1; Sol follow-up completed. Writer raw-cause test proves storage, not full writer-to-reconnect path; test name/claims must stay narrow. No original tests may change.
- Follow-up: Sol recorded read/framing and heartbeat failures before cleanup, added pending-reason tests, narrowed writer test name and propagated finite peer-handler assertion failures. Independent reviewer found a stale heartbeat timeout after caller abort could replace the synthetic cause. Root added a Cond-synchronized regression: before guard exit 2 (1 failed), after `!self.alive` guard exit 0 (1 passed). Evidence `_build/exec-causes/heartbeat-race-before.log` and `heartbeat-race-after.log`. Root reviewed this minimal guard; final integrated revalidation passed (C3 and R1).

## P1 — Local package hygiene (done)

- Owner: root. Local `moon package --list` exposed Python `__pycache__/*.pyc` in archive despite Git ignoring them. Add only cache exclusion patterns to `.moonignore`, repackage and inspect actual ZIP. No publication, dependency changes or original file deletion. Root already ran `moon info . --target native`; generated public API is unchanged.
- Actual ZIP inspected after exclusion: zero Python cache entries, new native tests included. Final archive and checksum recorded by `_build/exec-causes/acceptance.json`; no registry-install claim.

## R1 — Hosted CI handoff (done)

- Proposed action: commit/push reviewed J1+C1+package/contract changes plus the specifically disclosed preexisting EMQX fixture/doc patch to PR #2; run hosted CI only. Do not merge, tag, publish or operate hardware.
- Review artifacts: `_build/exec-causes/implementation.patch` (SHA256 `11d6a5975a2b449d17c3f066145748ab023faa2c624c04b919f3e6292514f81c`) and `_build/exec-jitter/preexisting.patch`. Existing user prompt explicitly prohibits remote push without authorization; continued local autonomy does not silently lift that boundary.
- User subsequently explicitly authorized this proposed commit/push, including the existing EMQX changes, and delegated routine reversible follow-through. Do not ask again for the same operation. No merge or release authorized. Pre-commit source/protected hashes rechecked against accepted manifests.

- Delivered commit: `d61eb67482f7bc947cf0b1eab3b2931e8ec993ec`, pushed normally to `codex/v0.3-mtls`. Accepted source bytes rechecked against the committed tree.
- Hosted [PR CI](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35354946801) and [push CI](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35354944159) both passed on that exact commit: Ubuntu/macOS native 54, broker 25, faults/scenarios/consumers; Ubuntu EMQX 4. Conditional macOS EMQX/latest-compatibility skips remain distinguished. Luna retrieved logs and root independently verified results.
- Evidence: `_build/exec-ci/summary.md` and per-run final JSON/logs. PR remains OPEN; merge/release/registry install not performed. Closing state/release-note update is documentation only.

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

## W1 — Native WS/WSS capability (done)

- WHY: next roadmap capability enables brokers exposed through native WebSocket endpoints. Base `0a604f478b7d938c9fad73b3c71c30992378f48a`, branch `codex/roadmap-native`, isolated worktree `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`.
- Root decides API/ownership; Sol `/root/jitter` requested high performs bounded read-only inspection of pinned async websocket and custom TLS integration before implementation dispatch. No recursion.
- Contract: MQTT subprotocol must be selected; only binary messages feed MQTT; WS message/frame boundaries do not imply MQTT packet boundaries; bounded buffering and control-frame handling, cancellation and closure preserve existing session semantics. TCP/TLS behavior remains available.
- Acceptance: native WS/WSS including CLI, independent broker interoperability, malformed handshake/non-binary/fragmentation/close and resource-bound tests, original native/broker checks. No claims from Python-only models.
- References: bundle ROADMAP P1/v0.4 and ACCEPTANCE_CRITERIA v0.4; OASIS MQTT 3.1.1 section 6; RFC 6455.

## Q1 — Reconnect-resilient QoS 1 (done)

- Depends on reviewed W1. Separate connection generation, logical session and delivery identity; retain outgoing in-flight only when protocol permits, explicit broker-session loss, packet ID and DUP rules, waiter/protocol lifetime separation. Root approves public contract before Sol implementation.

## D1 — Durable outbox beta (done)

- Depends on Q1 delivery semantics. Evaluate SQLite as single backend; stable ID, restart recovery, bounded storage, expiry, disk-full/corruption, documented duplicate window. No generic storage framework.

## M1 — Application-driven MQTT 5 subset (in_progress)

- Depends on Q1/D1 evidence. Scope reason codes, session/message expiry, negotiated receive/packet limits, user properties and request/response metadata. No QoS 2 or full-spec claim.

## H1 — Host-side HA/ESP32 consumer and reconciliation (host software done)

- Host-side implementation, simulator and instructions can proceed without hardware. Do not hardcode GPIO or equate PUBACK with physical completion; reconcile unknown delivery by correlated state queries. Board/HA access and actual published-package/hardware acceptance remain pending external prerequisites.

## R2 — Intermittent TLS 1.3 identity rejection classification (done)

- Base `0a604f478b7d938c9fad73b3c71c30992378f48a`, original `moon-mqtt-client` checkout. Sol `/root/causes`, retained explicit Sol/high request, owns runtime/TLS fix and new regressions; root semantic review.
- Evidence: final docs-head push run35355783844 macOS job105634673813 fails original missing-certificate assertion `tests/integration/run.py:676` (TlsFailure expected, ProtocolError observed). Do not retry away failure or alter original assertion/timeouts.
- Investigate first TLS CONNECT write/CONNACK read error path, especially raw socket errors. Preserve malformed-MQTT ProtocolError and cancellation behavior. Require deterministic regression and original failing integration entry, then integrated checks and fresh hosted CI.

- R2 delivered `eeb93be40ba19020df1a9effcdd8771c5f132104` with both hosted CI runs35356886328/35356879535 passing. Code integration into W1 owned by Sol.

### W1 implementation decision and ownership

- Stock pinned WebSocket cannot validate selected subprotocol or consume custom mTLS transport; its frame-header parse is not cancellation-resumable. Root approves a provenance-tracked native client subset of upstream frame engine over arbitrary Reader/Writer, bounded HTTP upgrade, exact mqtt subprotocol, secure entropy, server masking/text rejection. No general network framework. WS packet reads block until data/transport closure instead of reusing 100ms TCP idle cancellation.
- Public API: `NetworkTransport::{Tcp, WebSocket(String)}`, TLS remains independent; CLI `--ws-path /mqtt` combines existing TLS/identity flags. Root reviews public interface before acceptance.
- Sol `/root/jitter` owns implementation and NEW native tests; Luna `/root/baseline` owns NEW tests/ws_broker.py and deterministic Paho startup smoke. Root owns NEW tests/ws_interop.py and tests/ws_protocol_faults.py, contract/docs/state and integration. Existing acceptance programs remain unchanged.

### W1 independent review (in_review)

- Reviewer `/root/causes` retained requested Sol/high, independent of implementer `/root/jitter`. Root separately reviews config/adapter/runtime and exercises raw peer and real broker.
- Findings sent for fixes: serialize closed-state checks with frame writes to prevent data after Close; data/control payload EOF must stay a transport error (not successful truncation or terminal protocol fault); reject reserved close codes; remove unsupported generic Text handling from this MQTT-only fork; include rewritten upstream client source in provenance manifest.
- Root findings: encrypted WS upgrade must preserve R2 OSError/TLS/EOF classification; strict bounded HTTP host validation belongs only to WS; request target must be bounded/injection-safe; unsolicited extensions rejected.
- Broker fixture smoke: pinned ARM64 EMQX WS and mTLS WSS CONNACK passed with independent Paho2.1.0; missing-client-cert WSS refused. These are broker/fixture checks, not native-client acceptance.

### W1 external-consumer fixture correction

Full original check passed native70, broker25, faults10 and scenarios, then failed external-consumer compile: its fixed source filename list omitted `websocket_transport.mbt` and `internal/websocket`. Under the user's explicit authorization to handle analogous low-risk follow-through, root updated only local source copying: include root implementation `.mbt` files (excluding tests) and internal package subtree. Consumer program assertions, broker interaction, timeouts and registry path remain byte-for-byte unchanged. The original program remains retrievable from base0a604f4; no weakening/skipping. This necessary fixture evolution replaces the earlier blanket no-test-file-edit working constraint for this copying block only. Failure retained `_build/exec-ws/accepted-check.log`; subsequent full acceptance must supersede it explicitly.

- Additional root/implementer review found protocol-error notification could block on the write lock/socket and delay or mask an already-known framing cause. Root chooses synchronous fail-close on protocol violations (RFC6455 section7.1.7 notification is SHOULD with failure exceptions); normal DISCONNECT/peer-close retain Close handling. A deterministic transport seam must prove immediate close without waiting for a failing/blocked writer. This is a scoped MQTT adapter policy, not a generic RFC framework claim.

### W1 existing normal-scenario startup race

The next full check passed native71, broker25 and faults10 but failed normal-scenario ON feedback. Broker log `_build/exec-ws/scenario-startup-race-broker.log` proves the command was sent at1789743272 before device CONNECT/SUBSCRIBE at1789743273. The test used process liveness as readiness. Root adds a bounded barrier on device initial feedback (emitted only after command/query SUBACK), before launching the controller; all business assertions and existing8s budgets remain. No production command replay/retention changes. This fixture correction falls under the same routine follow-through authorization. Prior failing full log retained as `final-check.log`; final accepted run must use a distinct evidence name.

### W1 final local acceptance

- Root approved public API diff: only NetworkTransport and Config.transport additions. `moon info . --target native` matches that change.
- Full original check passed native71/broker25/fault10/scenarios/external TCP+mTLS consumers, then raw WS peers6/6 and real EMQX native WS/WSS4/4. Final logs `integrated-{check,protocol,interop}.log` under `_build/exec-ws`. Earlier failed logs are not overwritten.
- Cond-controlled post-Close writer regression fails when guards are removed and passes restored; protocol error immediate fail-close tested without a write attempt. Provenance fingerprints verified against pinned0.21.3. Local source package inspected for new code/license inclusion and cache exclusion.
- Base aligned to accepted R2 `eeb93be40ba19020df1a9effcdd8771c5f132104` while preserving tested source bytes. Branch `codex/roadmap-native` stays separate from v0.3 PR. Hosted CI required before W1 done.

### Q1 approved contract and implementation handoff

Root decision: `docs/architecture/RECOVERABLE-QOS1.md`. Preserve default clean-session publish behavior; opt-in resume mode uses a separate explicit delivery handle API, stable DeliveryId, original packet IDs/DUP, bounded state, waiter-independent lifetime and fail-closed broker-session-loss policy. No disk persistence/offline queue yet. Required raw-peer and real-broker evidence listed in the decision. Code implementation follows W1 hosted acceptance; root owns public contract, integration and final review.

### H1 intake findings (planned, no implementation yet)

Root read the original hardware plan as task material. Its direct-chain diagram and universal onboard-LED suggestion are superseded by current user constraints: all participants communicate through the broker, board/GPIO unknown. Current thermostat controller `ControllerState::next_command_id/next_query_id` restarts at cmd-1/qry-1, so process restart identity collision is a concrete software gap for H1. Preserve existing demo tests while adding stable process/session entropy and explicit expiry/reconciliation coverage in the eventual scoped implementation. Hardware, HA instance access and released-registry-package checks remain separate pending prerequisites.

### Q1 isolated implementation card (in_progress)

Owner `/root/jitter`, retained explicit Sol/high request, actual runtime model metadata unavailable. Workspace `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-qos1`, branch `codex/recoverable-qos1`, base6479bc22c782fb12ad52b80f2a4e2ea2a9f6fb98. Root owns integration and global exec files. WHY: separate delivery lifetime from connection/caller wait, while W1-specific closure and hosted failures are handled independently. W1 must pass before integration acceptance; this changes scheduling, not its gate.

Contract `docs/architecture/RECOVERABLE-QOS1.md` approved with review corrections: client-owned bounded delivery book; generation attachments; packet reservations; Resume subscription matrix; per-attempt ACK timeout and at most reconnect_attempts+1 attachments; terminal session-loss/exhaustion; waiter-independent completion. Preserve ordinary clean-session publish and existing tests. Source ownership: Q1 core client/runtime/session/types/wire, new delivery component/tests/CLI mode and relevant API docs in isolated worktree only. No WS/TLS changes, no dependency upgrades, no global exec edits, no recursive agents, no remote publication.

Acceptance: native deterministic lifetime tests, raw lost-PUBACK peer with same ID/DUP/order/session loss, waiter cancellation then ACK, capacities1, terminal cleanup, actual Mosquitto persistent session recovery, all existing checks; root independent semantic review before integration. Return actual commands/logs, API and commit/diff fingerprint, unresolved behavior. Escalate contract conflicts rather than weakening checks.

### W1 final closure and broker-readiness corrections

Root owns client/session/WebSocket changes; independent Sol `/root/causes` reviewed. Intermediate504a8af CI35361606190 passed, but Linux native delayed-forwarding evidence still showed raw MQTT data discarded by EMQX when immediate WS Close raced packet processing. Final behavior waits boundedly for peer close after MQTT completion, stops further MQTT writes, always closes locally, and preserves caller cancellation. Native peer-close/silent-peer/writer-spy regressions plus Linux before/after WS/WSS4 provide evidence. Original Session fixture literals restored after removing intermediate private hook.

Sol owns `tests/emqx_interop.py` startup readiness only: keep ACL/rejection assertions and timeouts, probe granted authorization and actual routed payload on hidden port, expose TCP gate only afterward and close it over restart. Dedicated listener-enable experiment was ineffective and discarded. Shutdown/join hardened after root resource review. Original4 and reconnect10 passed; root final verification/CI pending. Logs under `_build/exec-ws/` include failed diagnostic and successful corrected paths.

### D1 binding seam evidence (done, feature still planned)

Luna requested binding preserved; pinned sqlite3@0.2.2 passes fixed MoonBit check/build/native run, explicit transaction commit/reopen/exact BLOB/rollback, first with resolved async0.21.2 then explicit projectasync0.21.3. No project dependency change. `_build/exec-outbox/sqlite-probe/{compat-*,api-evidence.txt}`. Root reviewed async job cancellation/ownership source; planned concrete contract in `docs/architecture/DURABLE-OUTBOX.md`. Neither probe nor this plan claims durable delivery implemented.

### D1 concrete store implementation card (in_progress)

Owner `/root/causes`, retained explicit Sol/high request; separate worktree `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-outbox-store`, branch `codex/durable-outbox-store`, basee96a682b73e4056f5849369176fcafeed3e7f1f2. Independent bounded storage work can proceed alongside Q1 queue/watchdog fixes without sharing files; root integrates. Read approved DURABLE-OUTBOX decision and verified SQLite probe. Own new concrete store source/tests plus sqlite dependency/import declarations; no client/runtime/session/Q1, CI, global exec, release or recursive agents. No generic backend framework.

Deliver native async SQLite lifecycle, explicit schema/identity/ownership, records with persistent packet/DUP/attempt/expiry/order/blocked state, transactional admission/attach/may-write/ACK/block/never-started-discard, record/byte/page bounds, typed store errors and non-destructive corruption behavior. Do not claim network durability yet. Tests must cover reopen exact bytes, rollback/admission limits, expired-unsent versus quarantine-started, concurrent ownership/identity mismatch, SQLite Full and corrupt input, and resource cleanup. Root alone wires this concrete store into accepted Q1 after semantic review.

W1 accepted: e96a682b73e4056f5849369176fcafeed3e7f1f2, hosted35363101797 bothplatformspassed, includingUbuntuEMQX4/WS4. Rootverifiedhead/conclusions. Q1 firstcandidate858a90b reviewed: rootrequestedboundedpre-writequeuebudget, accuratecapacity1occupancy, cheapoversizeadmissionrejection, overflow-safeattemptbudget andtypedterminalcauseonhandle; Solfixinginitsisolatedworktree. D1concretestoreproceedsindependentlywithoutnetworkintegration.

Q1 integration in_review: root local integrated checks passed (STATE lists exact evidence); hosted acceptance pending. D1 store support interrupted by account limit; candidate preserved in isolated worktree, root taking over semantic review. M1 spec correction and D1 runtime review follow-ups did not execute due to the same limit.

D1 store seam in_review: integratedd2fa265/native94 pass, root concurrency defect fixed with before/after counterexample. Network integration pending. D1 runtime review was re-requested once after user go on; no repeated quota retries. M1 approved subset boundary now MQTT5-SUBSET.md, codec/runtime tasks remain planned.

### D1 runtime implementation card (in_progress)

Owner /root/jitter, retained requested Sol/high; actual runtime metadata unconfirmed. New isolated worktree `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-durable-runtime`, branch codex/durable-runtime, baseb8a7cc5. Root approved the completed read-only seam review: one concrete outbox and client-level gate; outer cancellation protection covers each DB commit and memory publication; recovered handles constructed before dial and returned with callback; no requirement for a separate pre-dial user callback. Add nested typed DurableStorage cause, block terminal rows before settlement, preserve pending rows on normal closure, close only after all store-using tasks end. Root owns integration and global state.

Scope core runtime/client/session/delivery/types, new durable API/driver/tests, necessary private fixture-field updates and public contract docs. Store internals may be minimally extended for targeted expiry/transition checks. No MQTT5, WS/TLS policy changes, old assertion weakening, global exec edits, push or recursion. Acceptance includes actual native process crash/reopen and retained broker session, no send after failed persistence, expiry/session-loss quarantine, cancellation/ACK ordering, bounded capacity and original regressions. Public recovered record inspection stays bounded and immutable; do not expose connection/store internals. W1/Q1 hosted regression currently being diagnosed by root is a final integration gate, not an excuse to overwrite transport policy.

H1 root-owned host implementation: isolatedbaseb8a7cc5, candidate8a51005, newnativecontroller/simulator/commonprotocol/README and independentbroker test. Nooriginaldemo/testassertions changed. Fourrealbrokerprocesscasespassed; integratedmandatorychecks/CI pending. Hardware/actualHA/publishedregistry gatesremainopen. Q1donebinds fb48772/CI35410891723 bothplatforms.

M1 codec root-owned isolatedworktree moon-mqtt-mqtt5/baseff2bcc8: newinternal/mqtt5+codecprobe/tests; packet/direction/property/reason validation and compactforms, outgoing3fieldreuse/incoming5parser. Native103passed andactualMosquitto/Paho5 bidirectionalmetadata passed. Lunaindependentboundedtableauditrunning; productionruntime/sessionnegotiation/durablemetadata notyetimplemented. Rootenforcesupdated first-connectionSP rule fromMQTT5section3.2.2.1.1.

### M1 runtime integration card (planned)

WHY: expose the accepted MQTT5 wire seam through the production client, preserve recoverable semantics and support request/response metadata. Owner Sol /root/jitter after D1 candidate handoff; requested gpt-5.6-sol/high, actual model unconfirmed. Root approves API/semantics and integrates; Luna /root/baseline (requested gpt-5.6-luna/medium) maps current callsites read-only. Runtime workspace/base will bind the integrated D1 candidate plus codec7297317; do not implement against stale D1 core.

Read docs/architecture/MQTT5-SUBSET.md and DURABLE-OUTBOX.md. Scope protocol types, adapter, handshake/read/write/request paths, delivery metadata/store schema and new focused tests/driver/docs. Do not modify WS/TLS ownership or global exec files. Preserve default3.1.1 and ordinary publish completion semantics, immutable expiry, same-ID/DUP, control priority and bounded admission. No compatibility schema migration, generic property map, automatic redirect/session reset, QoS2 or hardware.

Acceptance: explicit5 selection, typed numeric reasons incl negativePUBACK and0x10, incoming/outgoing metadata, negotiated ReceiveMaximum/packet/QoS/retain/keepalive, durable properties and known logical-session ownership, conservative terminal handling. Raw peer proves credit/control ordering, first-SP rejection, negativeACK, negotiated limits and expiry. Real Mosquitto/Paho proves production metadata and persistent/process recovery. Root runs integrated original3.1.1/D1/HA/WS checks and hostedCI. Escalate contract conflicts; return commits, actual commands/logs and unverified paths.

D1 independent reviewer /root/durable_review requested gpt-5.6-sol/high with fork none; spawn succeeded after earlier causes task disappeared from live-agent listing. Actual model metadata remains unavailable. Read-only current D1 patch review, no shared builds/broker/writes. Root independently prepares M1 raw-peer acceptance in tests/mqtt5_runtime.py; only Python syntax checked so far, driver/runtime are still absent and no passing M1 runtime claim is made.

M1 root acceptance assets prepared: untracked tests/mqtt5_runtime.py (Python syntax only), draft native driver under `_build/exec-mqtt5/runtime-driver-draft/` (not compiled; not in package). Planned detailed API names for driver alignment: Config.protocol/session_expiry_secs; Client.publish_detailed -> PublishReceipt; ClientError.BrokerRejected(BrokerReason)/ServerDisconnected(BrokerReason); DeliveryStatus.broker_reason; Message.properties. Implementer may propose a concrete improvement before driver integration; do not silently drop reason observability. The draft independently covers ReceiveMaximum=1 with inbound PUBACK while send credit is exhausted, negative PUBACK, fresh unexpected Session Present, ServerKeepAlive, QoS/retain/packet limits, compact terminal DISCONNECT and actual Mosquitto/Paho property roundtrip. Durable protocol5 process recovery/expiry and schema integration still need additional evidence.

M1 owner update: /root/durable_review (explicit Sol/high retained), newworktree moon-mqtt-mqtt5-runtime/base3b6ae8c; prior planned jitter ownership superseded. Root owns tests/mqtt5_runtime.py and production runtime driver. D1 jitter only fixes store-open TOCTOU and validates follow-up. Distinct worktrees, and M1 deliberately leaves SqliteOutbox::open unchanged until follow-up integration. No agent writes global state; no M1 self-review claim. Root will perform M1 semantics review and request independent Sol review when implementation is ready.

### M1-store parallel implementation card (in_progress)

Owner /root/jitter (retained requested Sol/high, actual model unconfirmed), isolated moon-mqtt-mqtt5-outbox/codex/mqtt5-outbox/baseb774c8a. WHY: concrete schema work has separate file ownership and a fixed interface, so it can proceed alongside M1 protocol/core without duplicating effort. This is the explicit useful-work exception to one Sol implementer; core owner confirmed durable_store.mbt unchanged and hands it off.

Sole files durable_store.mbt, new mqtt5_store_wbtest.mbt, existing internal/mqtt5 import only. Core runtime remains /root/durable_review-owned. PersistedDelivery gains properties:Bytes (canonical PUBLISH property section, default zero length byte) and message_expiry_at_ms:Int64?. admit keeps positional parameters plus optional named properties and message_expiry_at_ms. Add async has_known_session/mark_session_known independent of row count. Version2 rejects old/unknown schema without mutation; retains two-pass validation and exclusive ownership. Validate metadata through codec, preserve exact binary/order/deadline, include payload+property bytes in max_payload_bytes, and reject corrupt or inconsistent expiry presence. No broker tests or global exec edits. Native reopen/bounds/rollback/known-marker/schema1-byte-preservation regressions; hand a commit to core/root for integration.

D1 accepted b774c8aec35c8792d7be4688e38cdf271e34aa8e / hosted35413417731: bothplatforms native123, broker25/fault10/recovery5/durable4/HA4/codec/WSraw6; UbuntuEMQX4/WS4. Rootverified exacthead/job results andactual logs after Luna retrieval; `_build/exec-outbox/ci-b774c8a.*`. D1 milestone done, M1runtime remains in_progress.

M1 first candidates integrated: core5394f2d as5445587; store15eef3a asf1b2832. Root native combination failed in provisional-capacity fixture (expected Backpressure, actual NotConnected); implementation owner investigating negotiated-state initialization without weakening assertions. Root raw peers passed negative PUBACK, negotiated local limits and terminal DISCONNECT; actual Mosquitto/Paho metadata roundtrip passed. Logs `_build/exec-mqtt5/first-runtime-peers.log` and `disconnect-reason-peer.log`; the former contains a corrected driver-side disconnect-catch failure. Full M1 acceptance remains open.

M1 byte-budget decision: canonical empty property section contributes zero metadata bytes; nonempty entire section is charged. Original six-byte D1 bound stays unchanged. Root identified metadata decoding before aggregate/page bounds; store owner must reorder cheap validation and add nonempty metadata overflow/rollback coverage. See MQTT5-SUBSET.md.

M1 takeover / in_progress / root: store7302bcb (integratede5e21b5) passed isolated132 native; core interrupted snapshot63320c7 (integrated30dbd47) was incomplete and initially did not compile. Account-limit errors stopped both requested Sol core and Luna completeness check; actual model metadata remains unconfirmed. Root completed credit scheduling, positive reason and DUP handling, partial UNSUBACK completion, control packet size bound and synthetic-session field initialization. Native139 passed (`takeover-native4.log`), runtime10 passed (`production-runtime-first-full.log`); later mixed subscription case and original suite must pass on final bytes. Root directly reviewed bundle ROADMAP/ACCEPTANCE: no further required host milestone beyond M1, with release/registry and real hardware gates still separate.

M1 / in_review / root implementation head a46f0648f1c139d142e88e52116e2a4d38c5e119: complete local scripts/check.sh passed (native139, broker25, protocol faults10, scenario/external TCP+mTLS consumer, recovery5, durable4, HA4, codec roundtrip, M1 runtime11), final native140 with control bound regression, WSraw6 and actual EMQX WS/WSS4 passed. Additional raw reconnect test passed1/1: server credit drops2→1; both retained deliveries replay in order, same IDs/DUP, no quarantine and no second PUBLISH until first ACK. Final hosted-head gates pending. Logs in STATE.

M1 / done / root: exact-head hosted35414746155 passed macOS/Ubuntu, including runtime12 and native140, independent logs inspected. All selected host milestones closed. Live PR#2 is merged (external owner action), while registry0.3 is unavailable. Prepared and byte-verified two v0.3/TLS publication archives; no publication executed. STATE lists precise external prerequisites and artifact evidence.

V0.3 publication / done / root: latest explicit authorization exercised for prepared eeb93be packages. TLS0.1 then MQTT0.3 returned200 OK; unchanged fresh-module registry TCP/mTLS passed. Tagv0.3.0=a7fe723 (same accepted source tree), GitHub release2026-09-19T02:19:45Z, all4 public assets byte/hash verified. Main docs-onlycf883bf records completion; STATE links evidence. No real hardware or roadmap-feature publication claim.
