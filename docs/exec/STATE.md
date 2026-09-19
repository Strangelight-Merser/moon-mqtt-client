# Execution state

Updated: 2026-09-18. Expanded authorized host/software implementation: **done**. Roadmap release R7: **done**. W1/Q1/D1/H1 host software and M1 runtime are done. Actual HA/ESP32 gates remain open. The separately authorized v0.3 registry/publication gates are now done.

## Authorization and active baselines

The user explicitly requested autonomous continuation until the preceding plan is implemented and authorized routine reversible follow-through, commits/pushes and the disclosed fixture corrections. This supersedes the earlier single-milestone stopping point. The latest explicit user authorization now permits the prepared v0.3 publication sequence: async-tls0.1, MQTT0.3, registry acceptance, tag/GitHub release/assets. It does not authorize publishing the unrelated roadmap branch as0.3 or operating real hardware/production. Do not ask again for already-authorized routine work, and do not claim external gates are done.

- Original checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`: clean `codex/v0.3-mtls`, accepted `eeb93be40ba19020df1a9effcdd8771c5f132104`; Live GitHub verification: PR #2 was merged by Strangelight-Merser at2026-09-18T15:02:39Z, mergea7fe72350831f3af409f36b3c78499cb141bf111. Earlier open-candidate notes were stale; root did not perform this merge.
- Integration checkout `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`: `codex/roadmap-native`, release candidate `ce77a3c3493a292549a538af394ac175bf47dce7`, merged/tagged as `bc93570ebd3e3034511126342f84c87876088ebc`. Root owns integration and both exec files. Do not expand PR #2 with this branch.
- Latest hosted accepted release candidate is `ce77a3c3493a292549a538af394ac175bf47dce7`, runs35415955398/35415973508, macOS and Ubuntu success. Runtime bytes equal M1 d5e5fb0; merge/tag bc93570 has exactly the candidate Git tree. Any following state-only commit does not change implementation bytes.
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

Status: done. Contract `docs/architecture/MQTT5-SUBSET.md`. The required host roadmap ends at this application-facing MQTT5 subset; later Topic Alias, Subscription Identifiers, QoS2, generic storage, browser/MCU and durable inbox remain non-goals. Actual HA/ESP32 and publication/install gates below cannot be marked complete by host tests.

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

Hosted35414746155 passed at exact headd5e5fb01f33358843fb1e251ad86b03acd687bea. Root downloaded the job JSON/full logs and independently checked head, conclusions and test evidence: both platforms native140, broker25, fault10, separate TCP/mTLS consumer, codec roundtrip, production MQTT5 runtime12, HA4, recovery5, durable4 and WSraw6; Ubuntu additionally EMQX4 and WS/WSS4. Latest-compatibility is conditionally skipped by the existing push workflow, not claimed tested. Logs `ci-d5e5fb0.{json,log}` and `ci-d5e5fb0-summary.txt`. Root review binds baseb774c8a to sourcea46f064 with SHA256 in `root-review.txt` and `reviewed-m1.patch`. No source changes after accepted head.

## Dispatch, ownership and remaining gates

All model labels are requested bindings; tools returned no confirming runtime-model metadata. Root actual model also unconfirmed. No reset credit was redeemed.

- Root/Astra: contracts, direct diff review, integration, credit/DUP/reason corrections after interruption, independent peers/broker/process acceptance, global state.
- /root/durable_review: requested gpt-5.6-sol/high, independent D1 review followed by M1 core implementation (not an independent M1 review). Interrupted by account usage limit, preserved63320c7.
- /root/jitter: requested gpt-5.6-sol/high, Q1/D1 and disjoint M1 store implementation; store complete7302bcb. Its native132 evidence is isolated, not final integration acceptance.
- /root/baseline: requested gpt-5.6-luna/medium, earlier CI/spec/API evidence. Earlier bounded plan-completeness audit failed on account usage limit; root read bundle ROADMAP and ACCEPTANCE directly. Later R7 docs audit succeeded, recorded below. No result fabricated.
- Historical /root/causes: requested Sol/high, TLS/store and W1 review; earlier quota interruptions preserved in history.

No recursion, shared-file writers or concurrent fixed-port broker suites. Root owns active integration `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap`; agent worktrees are preserved. Default broker slot is free after local acceptance. Root completed final CI retrieval directly after model usage limits.

External gates remain: actual HA instance and exact ESP32 board/pins/network/hardware acceptance. v0.3 and v0.7 registry installation, integration and publication are completed below. PR#2 is already merged externally; no repeat merge needed. Workspace consumer success does not satisfy registry install. No credentials/GPIO assumptions or actual hardware claims. W1 historical hosted failure7d01032/run35410348751 remains cause-unconfirmed; later accepted routing-readiness fix does not retroactively establish that exception. Original ZIP and original checkout remain preserved; PR#2 is separate.

Next handoff: all authorized host implementation and publication is complete through v0.7.0. Do not expand non-goals. To start real-consumer acceptance, obtain actual HA access and ESP32 model, pinout and network details, then follow examples/ha_relay/README.md and the original hardware test plan. Hardware remains blocked on those external inputs; no further host-software milestone is outstanding.

Pre-publication registry evidence (superseded by successful installation below): original unchanged consumer_smoke.py --registry at eeb93be failed after a successful registry refresh: no version satisfies moon-mqtt-client@0.3.0. mTLS registry stage consequently did not run. At that pre-publication check GitHub latest release wasv0.2.0. Log `registry-v03-live-check.log`; local source consumer success is not substituted for this gate.

Prepared concrete v0.3 publication candidates from already-merged eeb93be (not the unreleased MQTT5 branch): `_build/exec-mqtt5/release-candidates/Strangelight-Merser-moon-mqtt-client-0.3.0.zip` and `Strangelight-Merser-async-tls-0.1.0.zip`. ZIP integrity and every extracted file were verified against that exact Git source (81 MQTT files,18 TLS files); SHA256 and staging path are in `release-candidate-archives.json`. Direct nested workspace TLS packaging produced an empty ZIP despite successful check; that rejected artifact/log is retained separately. Rebuilding the exact TLS source in a standalone temporary module produced the verified package. Publish TLS from that standalone module, then MQTT from the merged source, then run fresh registry TCP/mTLS consumer, tags/releases/assets only when authorized. These preparation notes preceded explicit authorization; the publication is now complete below. Current roadmap package version was not silently bumped or published as0.3.

Release follow-through authorization (now completed): user explicitly authorized the previously prepared publication. Rechecked both archive hashes and standalone TLS source bytes; main stilla7fe723 and no v0.3 tag/release exists. Publish exact accepted eeb93be candidates in dependency order, then registry acceptance before release completion.

## v0.3 publication completion — done

User explicitly authorized the prepared packages. Root published async-tls0.1.0 from the standalone module, then MQTT0.3.0 from accepted eeb93be; both server responses200 OK. The original unchanged `tests/consumer_smoke.py --registry` passed TCP and mTLS QoS1 round trips in fresh temporary modules, without a local source workspace. Previous missing-version evidence is retained as history, not a current blocker.

[v0.3.0 release](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.3.0) published2026-09-19T02:19:45Z. Tagv0.3.0 targets mergea7fe723; source tree1e54ac08f5e07323a609f3cc3dc89ba6d526baf5 equals accepted eeb93be. Four assets (TLS ZIP, MQTT ZIP, source archive, SHA256SUMS) downloaded anonymously and verified byte-identical and against GitHub digests. One urllib TLS-handshake timeout was resolved using bounded curl IPv4 download with normal certificate checks; no integrity check was bypassed.

Evidence in `_build/exec-mqtt5/`: `publish-tls01-result.json`, `publish-mqtt03.log`, `registry-v03-published-acceptance.log`, `github-release-v03.json`, `public-download-verification.json`, `release-assets/`. Both package hashes equal the preauthorized candidates. Main release documentation updated in docs-onlycf883bf; original code checkout remains untouched. Original ignored paused release state was backed up then marked complete. This release does not include or merge the later roadmap branch. No further publication action is pending for v0.3.

## R7 roadmap publication — done

The latest user request to continue the remaining work, following explicit publication authorization, authorizes integrating and publishing the completed roadmap. Root selects v0.7.0, the planned endpoint; W1/Q1/D1/H1/M1 ship together, without invented v0.4–v0.6 releases. This is packaging/version integration, not a new architecture expansion or hardware authorization.

Base adee7c6; merged main cf883bf as5141eef. Only STATE/TASKS conflicted; retained the authoritative roadmap state, which already contains completed v0.3 publication evidence. Runtime .mbt/moon.pkg bytes remain identical to tested d5e5fb0. Candidate updates version/description, stale capability docs, includes shipped architecture contracts, and adds standalone package/registry consumer verification reusing unchanged runtime, WS/WSS and HA assertions.

Luna /root/baseline (retained requested gpt-5.6-luna/medium, actual metadata unconfirmed) successfully audited release-facing docs this turn; identified obsolete MQTT5 unsupported/WS development statements and overbroad full-MQTT5 wording. Root implements release integration and validation. Package consumer copies executable entry sources only; runtime and example libraries come from the extracted ZIP before publication or Mooncakes afterward. No local-library substitution is permitted in registry mode. Extracted-package consumer passed26/26 behavioral cases (no skips); `packaged-consumer.json` records source=extracted package and workspace_override=true. Registry acceptance passed26/26 with source=Mooncakes registry and workspace_override=false; original separate TCP/mTLS registry consumers also passed. Evidence `_build/release-v0.7.0/` in the roadmap worktree.

Candidate ce77a3c passed both push35415955398 and PR35415973508 CI on macOS/Ubuntu. Root inspected actual logs: native140, broker25, faults10, codec/runtime12, HA4, recovery5, durable4, WS peers6; Ubuntu also EMQX4 and WS/WSS4. Optional latest-compatibility was not run. PR#3 merged at2026-09-19T02:37:42Z asbc93570; candidate/merge tree both3aab0b04dbbec4bc6fe955f2c096a63cbd817f48. All156 ZIP entries match the candidate Git source. `moon publish` returned200 OK, then fresh registry TCP/mTLS and26 roadmap cases passed with FD-leak checks enabled, no skips or local library overrides. TLS0.1 was reused from the registry, not republished.

Tagv0.7.0 targetsbc93570. GitHub release published2026-09-19T02:39:35Z. Package SHA256ac729cea459e932bd60af56950a91968df87949d33c99ab1dcdfc36937dfb665; source archive30975842ae213edefcf24d5c4b04a77a826c94b23430c3052b7a79a0ec0ebd9c. Evidence: `ci-{pr,push}.json`, `ci-pr.log`, `package-verification.json`, `publish.log`, `registry-default.log`, `registry-roadmap.{json,log}`, `github-release.json`, `public-download-verification.json`. Final documentation commit does not alter published source/tag. Original checkout and research ZIP preserved.

Public asset acceptance: all3 v0.7 assets were downloaded anonymously and byte-identical to local artifacts and GitHub SHA256 digests. The first SHA256SUMS request hit a45-second network timeout; a bounded retry succeeded, with normal TLS verification. No package or tag was changed.
