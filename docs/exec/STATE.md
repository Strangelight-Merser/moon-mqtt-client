# Execution state

Updated: 2026-09-18. Expanded software roadmap: **in_progress**. W1/Q1/D1/H1 host software are done; M1 runtime is in_progress. Actual HA/ESP32, registry-install and publication gates remain open.

## Authorization and active baselines

The user explicitly requested autonomous continuation until the preceding plan is implemented and authorized routine reversible follow-through, commits/pushes and the disclosed fixture corrections. This supersedes the earlier single-milestone stopping point. Merge, tags, registry/GitHub publication, production and real hardware operations remain outside the recorded authorization. Do not ask again for already-authorized routine work, and do not claim external gates are done.

- Original checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`: clean `codex/v0.3-mtls`, accepted `eeb93be40ba19020df1a9effcdd8771c5f132104`; PR #2 remains the separate v0.3 candidate.
- Integration checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`: `codex/roadmap-native`, M1 candidate implementation head `a46f0648f1c139d142e88e52116e2a4d38c5e119` before this state update. Root owns integration and both exec files. Do not expand PR #2 with this branch.
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

## M1 current acceptance

Status: in_review. Contract `docs/architecture/MQTT5-SUBSET.md`. The required host roadmap ends at this application-facing MQTT5 subset; later Topic Alias, Subscription Identifiers, QoS2, generic storage, browser/MCU and durable inbox remain non-goals. Actual HA/ESP32 and publication/install gates below cannot be marked complete by host tests.

Implementation: codec7297317; public metadata22af25c and property serializationec61a5c/a140a22; core5394f2d integrated5445587; schema215eef3a integratedf1b2832; store follow-up7302bcb integratede5e21b5; interrupted core snapshot63320c7 integrated30dbd47. Root completed candidatef546452 after account limits stopped agents. The interrupted snapshot was incomplete and did not compile; no acceptance is attributed to it.

Delivered paths: explicit Mqtt5 selection (default311), independent5 decoder, detailed numeric broker reasons, mixed subscription/unsubscription acknowledgements, ordinary and durable publish metadata, immutable expiry, persisted known logical-session ownership including empty outboxes, negotiated packet/QoS/retain/keepalive and writer-side Receive Maximum credit. Durable positive/negative ACK deletes commit before packet ID release and handle completion. Negative ACK is Rejected(BrokerReason); positive0x10 is retained. First5 Session Present without local ownership closes. Server DISCONNECT preserves its reason and stops this scope.

Root semantic review corrected incomplete/incorrect paths: Receive Maximum was an admission cap and rejected valid replay queues; it now gates only unacknowledged QoS1 writes while local max_inflight and queues bound admissions. A parked FIFO head counts toward the existing queue bound; control traffic remains prioritized. Success handles preserve broker reason; first-write DUP is captured before possible-write persistence. Mixed negative UNSUBACK returns every detailed result, removes only successful desired subscriptions and does not exit the reader. Control packets use the negotiated maximum size. Local limit rejection never fabricates a broker ACK. Synthetic native clients receive required negotiated/session fields, without changing assertions or limits.

Store bounds: canonical empty property section costs zero metadata bytes; each nonempty section is fully charged. Same SQL/admission/reopen rule retains original D1 six-byte budget. Cheap aggregate/record/page bounds precede per-row metadata decoding; admission checks lengths first. Schema2 rejects old/unknown files without migration or destructive reset.

Actual local evidence under `_build/exec-mqtt5/`:
- `integrated-check.log`: complete scripts/check.sh exit0, native139, broker25, faults10, scenarios and separate TCP/mTLS consumer, recovery5, durable4, HA4, codec Mosquitto/Paho roundtrip and runtime11.
- `final-native.log`: native140 after an additional negotiated-control-size regression; `final-check.log`: zero errors. Later changes before hosted validation are fixture/format/doc changes unless separately recorded.
- `ws-protocol.log`: raw framing/cancellation6 passed. `ws-interop.log`: actual EMQX native WS/WSS4 passed.
- `production-runtime-first-full.log`: first runtime10 passed. `reconnect-credit.log`: separately verifies replay with lower negotiated credit and same packet IDs/DUP (passed1/1, no implementation changes).
- Runtime driver `examples/mqtt5_runtime_driver`, independent tests `tests/mqtt5_runtime.py`, public contracts README/API-CONTRACT, mandatory local/CI gates are integrated. Broker process recovery uses the ordinary production binary, SIGKILL after broker ACK suppression, independent Paho decoding, exact binary/repeated metadata, same packet ID/DUP and strictly decreasing expiry.
- Earlier `first-combined-native`, `credit-native`, `takeover-native2` failures are retained: missing synthetic negotiated state and an unnecessary clock read changed the deterministic expiry seam. Fixed without changing original assertions/time budgets. Do not cite those runs as passes.

Hosted final-head acceptance is pending. The accepted previous hosted head remains D1 b774c8a/run35413417731; it does not endorse M1.

## Dispatch, ownership and remaining gates

All model labels are requested bindings; tools returned no confirming runtime-model metadata. Root actual model also unconfirmed. No reset credit was redeemed.

- Root/Astra: contracts, direct diff review, integration, credit/DUP/reason corrections after interruption, independent peers/broker/process acceptance, global state.
- /root/durable_review: requested gpt-5.6-sol/high, independent D1 review followed by M1 core implementation (not an independent M1 review). Interrupted by account usage limit, preserved63320c7.
- /root/jitter: requested gpt-5.6-sol/high, Q1/D1 and disjoint M1 store implementation; store complete7302bcb. Its native132 evidence is isolated, not final integration acceptance.
- /root/baseline: requested gpt-5.6-luna/medium, earlier CI/spec/API evidence. Latest bounded plan-completeness audit failed on account usage limit; root read bundle ROADMAP and ACCEPTANCE directly. No result fabricated.
- Historical /root/causes: requested Sol/high, TLS/store and W1 review; earlier quota interruptions preserved in history.

No recursion, shared-file writers or concurrent fixed-port broker suites. Root owns active integration `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`; agent worktrees are preserved. Default broker slot is free after local acceptance. Final CI retrieval can be performed directly if models remain unavailable.

External gates remain: actual HA instance and exact ESP32 board/pins/network/hardware acceptance; merge/tag/publication authorization; clean installation of actually published async-tls and MQTT packages (TLS first). Workspace consumer success does not satisfy registry install. No credentials/GPIO assumptions or actual hardware claims. W1 historical hosted failure7d01032/run35410348751 remains cause-unconfirmed; later accepted routing-readiness fix does not retroactively establish that exception. Original ZIP and original checkout remain preserved; PR#2 is separate.

Next: finish exact-head hosted macOS/Ubuntu acceptance, resolve any real failure, then mark M1 and authorized host roadmap done and record precise external prerequisites. Do not start non-goal expansion.
