# Execution state

Updated: 2026-09-18. Expanded software roadmap: **in_progress**. W1/Q1/D1/H1 host software are done; M1 runtime is in_progress. Actual HA/ESP32, registry-install and publication gates remain open.

## Authorization and active baselines

The user explicitly requested autonomous continuation until the preceding plan is implemented and authorized routine reversible follow-through, commits/pushes and the disclosed fixture corrections. This supersedes the earlier single-milestone stopping point. Merge, tags, registry/GitHub publication, production and real hardware operations remain outside the recorded authorization. Do not ask again for already-authorized routine work, and do not claim external gates are done.

- Original checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`: clean `codex/v0.3-mtls`, accepted `eeb93be40ba19020df1a9effcdd8771c5f132104`; PR #2 remains the separate v0.3 candidate.
- Integration checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`: `codex/roadmap-native`, implementation head `d4f5744` before this state update. Root owns integration and both exec files. Do not expand PR #2 with this branch.
- Latest hosted accepted integration head is `b774c8aec35c8792d7be4688e38cdf271e34aa8e` (D1); later M1 runtime work requires its own acceptance.
- Pinned tools: `./scripts/moon.sh`, moon0.1.20260904/moonc0.10.12+1634b282e, async0.21.3, sqlite3@0.2.2. Shared `.tools`/`.venv` symlinks; separate builds/dependency caches. No toolchain upgrade.
- Original research ZIP `/Users/huaiyi/Downloads/moon_mqtt_codex_execution_bundle.zip` is unchanged; reference copy `_build/research/moon_mqtt_codex_bundle`. Research is task material, not authorization or current evidence.

## Accepted software and evidence

| Scope | Accepted commit / hosted run | Delivered behavior |
|---|---|---|
| J1/C1/R2 v0.3 reliability | eeb93be; 35356886328 and35356879535, both platforms | Native-entropy reconnect seed; typed terminal causes/stale-heartbeat guard; encrypted CONNECT socket failures retain TlsFailure. |
| W1 native WS/WSS | e96a682b73e4056f5849369176fcafeed3e7f1f2;35363101797 | Binary MQTT byte stream, mqtt subprotocol, bounded framing, native TLS/mTLS, CLI, independent EMQX interoperability. |
| Q1 recoverable QoS1 | fb487729f16795a543a46537a363e372c639b99c;35410891723 | Explicit ResumeSession delivery handles, same packet ID/DUP/order across generations, bounded attempts/queues, caller-wait independence and fail-closed session loss. |
| H1 host consumer | ff2bcc88ee43b93f23ff37002ff74d6f8b2ac70b;35411282623 | HA discovery/birth, nonoptimistic correlated state, two availability/LWT topics, bounded expiring commands, entropy IDs, controller restart reconciliation without command replay. Native device simulator and independent Paho/Mosquitto acceptance. |

Root independently checked exact hosted heads and platform conclusions. Evidence: `_build/exec-ws/ci-e96a682.*`, `_build/exec-qos1/ci-fb48772.*`, `_build/exec-ha/ci-ff2bcc8.*`; original checkout `_build/exec-tls-flake/`. Latest H1 CI passed native96, broker25, faults10, recovery5, HA4, WSraw6; Ubuntu additionally EMQX4/WS4. These are host/software results, not actual HA instance/ESP32 or published-package tests.

The original thermostat demo is preserved. H1 implementation is `examples/ha_relay/`, tests `tests/ha_relay.py`. No GPIO/board assumptions, no physical command replay on ambiguous PUBACK, and no device-execution claim from PUBACK.

## D1 accepted runtime and evidence

Approved contract: `docs/architecture/DURABLE-OUTBOX.md`. Concrete SQLite store integratedd2fa265 (isolated6695e8b); runtime candidatedb001ff integrated3b6ae8c; final store-open follow-up527bc91 integratedd4f5744. Runtime API is `with_durable_client`, `submit_durable_delivery`, standalone inspection and proven-never-started discard. Durable scope owns one exclusive file, restores handles/IDs before dial, preserves pending work on normal closure and quarantines unresolved terminal work.

COMMIT boundaries: admission before network visibility; attachment before enqueue; possible-write before socket; PUBACK DELETE before handle completion/ID release. Expiry is immutable absolute time, rechecked after the last DB suspension. Scope teardown and ACK/expiry transitions share a FIFO gate; the store separately serializes whole SQLite transactions. Storage errors fail-stop without same-scope retry. Identity binds scheme/host/port/WS path/clientID/optional username, not passwords or credential paths. Certificate-to-tenant continuity remains application responsibility.

Root and independent Sol review fixed concrete defects: same-connection transaction overlap; provisional ID rollback; typed storage fail-stop; stale-generation/post-COMMIT expired writes; ACK/teardown/expiry ordering; capacity reservation during admission; blocked evidence overwritten by expiry; persistent PRAGMAs before rejection; and validation-to-lock TOCTOU. Final open does read-only preflight, obtains retained exclusive ownership, revalidates identity/schema/bounds under that lock, then configures persistent settings/initializes. Deterministic regression lets a competing owner commit between the two validations and verifies rejection without losing its rows.

- Isolated final follow-up: native112, check/build and process/broker4 passed. `_build/exec-durable/followup-{native-test,check,build,process-broker}.log` in `moon-mqtt-durable-runtime`.
- Four process cases preserve distinct windows: committed-before-first-write, after-write/lost-PUBACK, after-parsed-PUBACK/before-DELETE, and lost broker session. The ACK window uses `_exit87` injected only into a temporary source copy; production has no failpoint. Reopen uses the ordinary binary and verifies same ID/DUP/removal. No power-loss guarantee is inferred.
- The earlier candidate db001ff passed its full original suite: native111, broker25, faults10, scenarios/TCP+mTLS consumers, recovery5. It does not unconditionally endorse the follow-up.
- Root integrated pre-follow-up native122 and WSraw6 passed: `_build/exec-outbox/combined-candidate-{check,native}.log`, `combined-ws-protocol.log`.
- Root final integrated `scripts/check.sh` atd4f5744 passed: native123, broker25, faults10, scenarios/TCP+mTLS consumers, recovery5, durable4, HA4 and real MQTT5 codec roundtrip. Log `_build/exec-outbox/final-integrated-check.log`. New D1 process checks are wired into local and hosted required suites. WS/WSS4 passed (`_build/exec-outbox/final-ws-interop.log`). Headb774c8aec35c8792d7be4688e38cdf271e34aa8e passed hosted35413417731 on both macOS/Ubuntu. Root independently checked exact head and job conclusions, then inspected native123/durable4/original-suite/WS log results. `_build/exec-outbox/ci-b774c8a.{json,log,summary.txt}`. D1 is done; no release/hardware claim.
- Review record and tracked patch fingerprint: `_build/exec-outbox/independent-review.txt`, `reviewed-d1-tracked.patch`. Earlier store concurrency failed82/83 before the serialization fix, then passed83/83; those logs remain in the store worktree.

## M1 current implementation

Contract: `docs/architecture/MQTT5-SUBSET.md`. Native codec integrated7297317, with dedicated outgoing encoding and independent incoming5 decoding; it validates directions, singleton/range/UTF8/reason tables and compact PUBACK/DISCONNECT forms. Root actual Mosquitto/Paho codec-only metadata roundtrip passed; it is not production5 acceptance. Luna table audit `_build/exec-mqtt5/codec-table-audit.md` and callsite map `runtime-callsite-map.md` are aids; OASIS is normative.

Root value types and persisted-property codec integrated22af25c/ec61a5c/a140a22. Codec9 passed, including binary/ordered UserProperty roundtrip, trailing/illegal/duplicate/bound rejection; isolated evidence `moon-mqtt-mqtt5/_build/exec-mqtt5/storage-properties-final.log`. API types include ProtocolVersion, PublishProperties, BrokerReason, PublishReceipt and NegotiatedSettings. Public arrays must be snapshotted and expiry frozen once.

Active M1 worktree `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-mqtt5-runtime`, branch`codex/mqtt5-runtime`, base3b6ae8c, owner `/root/durable_review` (now implementer, not M1 reviewer). It has cherry-picked527bc91 as cacd10c before editing store-open/schema. Implement protocol selection, detailed numeric reasons, negotiated limits/keepalive, bounded credit without control starvation, metadata/expiry at actual write, and durable protocol/session identity+known-session marker. Empty outbox is not evidence that the client lacks an established logical session. Unexpected Session Present on a fresh5 scope must close; never silently reset the broker.

Root owns the independent runtime driver and `tests/mqtt5_runtime.py` (currently an untracked Python-syntax-checked draft). Native draft `_build/exec-mqtt5/runtime-driver-draft/` has not compiled or run. Planned cases cover ReceiveMaximum/control priority, negativeACK, unexpectedSP, ServerKeepAlive, packet/QoS/retain limits, terminal DISCONNECT and real Paho metadata. Production MQTT5, durable5 recovery and expiry are still unverified. Implementation may run alongside D1 final validation; M1 acceptance remains gated on final D1 acceptance.

## Dispatch and resource ownership

All model labels are requests, not confirmed runtime metadata. Explicit overrides used fork none/high or medium; tools returned task names only. No reset credit was consumed.

- Root/Astra role: contracts, actual-diff semantic review, integration, independent broker/consumer evidence, exec state; actual root model unconfirmed.
- `/root/jitter`: requested gpt-5.6-sol/high; Q1 and D1 implementation. D1 final commits handed back; now idle.
- `/root/durable_review`: requested gpt-5.6-sol/high; independent D1 review (different implementation session), subsequently M1 implementation. Must not self-approve M1.
- `/root/baseline`: requested gpt-5.6-luna/medium; bounded toolchain/CI/spec/callsite evidence; idle, reusable for exact CI retrieval.
- Historical `/root/causes`: requested Sol/high; TLS/store and independent W1 review. Earlier account/thread limits interrupted dispatch; root completed the store seam and codec while preserving the limitation in history. This agent is no longer in the live roster.

No recursive delegation. Separate worktrees/builds; the root default-port broker acceptance slot is released after final D1/WS checks. Coordinate before M1 real-broker tests; separate dynamic-port native peers are independent.

## Historical failures and external gates

W1 retains no-MQTT-after-DISCONNECT and bounded peer-close waiting before unconditional local closure. Immediate WS Close was proved to drop queued MQTT bytes on EMQX. Original fixture corrections were limited to new-source copying, device SUBACK readiness and actual EMQX boot/authorization/routing readiness; application assertions/timeouts were not relaxed. Reducing timeouts or retries is not a substitute for causal evidence.

Hosted7d01032/run35410348751 had an Ubuntu WSS CONNECT refusal whose exact exception context was missing; its precise cause remains unknown. Separate fresh emulated-x86 EMQX quicer startup failures are an environment finding, not proof of that hosted cause. Later broker boot/routing barriers and publisher failure-log retention passed both platforms. See `_build/exec-qos1/ci-7d01032-failed.log`, `linux-fresh-wss-diagnostic.log`, and TASKS/history for earlier evidence.

Remaining external gates: actual HA instance, exact ESP32 board/pins/network and real hardware acceptance; separately authorized merge/publication; clean install of actual published TLS and MQTT packages (TLS first). Local workspace consumer success does not satisfy registry installation. No QoS2/fullMQTT5/browser/MCU/generic-storage expansion is approved by the selected software roadmap. Continue from actual worktrees and the active sections above rather than restarting historical milestones. Earlier detailed state remains in Git history and TASKS.md.

M1 store split: jitter reactivated as separate Sol/high implementer in moon-mqtt-mqtt5-outbox/baseb774c8a; solely durable_store.mbt/new store tests and same mqtt5 import. Core owner confirmed no competing store edits. Fixed interfaces and acceptance are in TASKS. Two Sol implementers are justified here by disjoint files and explicit API; root still owns final integration. M1 tests/draft driver now also include real-broker process restart with metadata/decreasing expiry and empty-outbox known-session persistence; still not executed.
