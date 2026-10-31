# Pro audit implementation

This is the current implementation record, separate from historical acceptance
reports. The approved scope is the full Pro audit and revised sections 6–16,
including the supplied H01 addendum. Later corrections take precedence.
Implementation is tracked in draft PR #6; the approved work-package labels
PR #6/#7/#8 do not predict GitHub numbering.

Baseline: `f0a0e9ec33b7aee2aeee26ea20572904d05e3069`, tree
`71f79cb5e42a2e93fbf63e3a2a84bde5457d074d`. Work branch:
`codex/pro-audit-implementation`. Original working trees are preserved.

The original addendum ZIP SHA-256 is
`50659953356b9195b1f1f5297d0d707e4eb5cfd7c9e75b54d05501b1369880d5`.
Baseline source, inputs, command logs and environment manifests are retained in
`_build/pro-audit/`. These are local evidence, not published assets.

## Requirements and acceptance ledger

Allowed statuses are **已验证完成**, **已实现待验收**, **等待现场条件**,
**等待 Pro 决策**, and **明确后续研究**. A source file or a test definition alone
does not establish acceptance. Each completed row must link execution evidence.

| ID | Requirement / batch | Acceptance | Status / evidence |
|---|---|---|---|
| A01 | Snapshot main/tree, originals, tools, dependencies, brokers | Hashes and baseline archive | 已验证完成 — [baseline](evidence/baseline.json) |
| A02 | Full requirements traceability and evidence inventory | Every recommendation classified | 已实现待验收 — final evidence inventory and statuses pending |
| H01 | Native production reproduction; bounded deferred business buffer | Original assertion red then green; no API/schema change | 已验证完成 — [native red/green](H01.md) |
| H02 | Public API Receive Maximum=1 peer | Admission barriers; SUB/UNSUB pass; PUBLISH stays blocked; no credit from SUBACK/UNSUBACK | 已验证完成 — [raw-peer red/green](H01.md) |
| H03 | Capacity, FIFO, control priority, cancel/close/generation | Targeted invariants and existing regressions | 已验证完成 — [native/core regression](evidence/core-after-dependency-fix.json) |
| C01 | Failure matrix by dial source and operation stage | Typed causes/numeric reasons; table-driven current-policy tests | 已验证完成 — [18 phase cases](evidence/dial-policy.json), [matrix](../../FAILURE-MATRIX.md) |
| C02 | TLS negative cases | CA, hostname, client certificate, key mismatch | 已验证完成 — [first integration](evidence/integration-first.json) |
| C03 | Hostile frames and bounded resources | Length/properties/flood/partial frame/reconnect/reason text | 已实现待验收 — hostile peers implemented; final Release execution pending |
| C04 | Recovery conservation and crash windows | Archive/retirement/no-overwrite/journal/native old-new checks | 已验证完成 — [clean operator acceptance](evidence/first-operator-consumer.json) |
| C05 | Storage failures | Isolated ENOSPC and child syscall fsync/EIO injection | 已验证完成 — [Mac ENOSPC/EIO](evidence/storage-faults.json); final platforms pending |
| C06 | Recovery support/security documentation | Experimental mutation, TCP scope, sensitive archives | 已验证完成 — [security boundary](../../SECURITY-BOUNDARIES.md) |
| C07 | User delivery guide and failure semantics | Ordinary/recoverable/durable; admission/ACK/cancel; no inbox promise | 已验证完成 — [delivery guide](../../DELIVERY-GUIDE.md) |
| D01 | Shared Core/Integration/Release scenarios | Local and CI use one list; default check stays Integration | 已验证完成 — [Core run](evidence/core-after-dependency-fix.json); [levels](../../ACCEPTANCE.md) |
| D02 | Candidate library consumers | H01 and maintenance driver compile/run from unpacked dependency | 已验证完成 — [first candidate consumer](evidence/first-library-consumer.json); final freeze pending |
| D03 | Versioned operator ZIP | Clean installation, all commands, docs/dependencies/checksums | 已验证完成 — [first operator consumer](evidence/first-operator-consumer.json); final freeze pending |
| D04 | Post-publication registry program | No MOON_WORK/source substitution; actual execution after publication only | 已实现待验收 — registry-only program prepared; actual run follows approved publication |
| D05 | Current release/candidate/support status | One current status record; historical reports unchanged | 已验证完成 — [current status](../../CURRENT.md) |
| D06 | Branch governance proposal | Read rules; prepare PR/check/force-push/delete protections | 已验证完成 — [proposal, not applied](GOVERNANCE.md) |
| D07 | Exact candidate Linux/macOS Release | First failures, retries, SHA/platform/broker logs retained | 已实现待验收 — final candidate platform runs pending |
| E01 | White-box fixtures and feature test grouping | Original assertions retained, baseline diff | 已验证完成 — [fixture inventory](evidence/fixture-migration.json) |
| E02 | Small process/broker/port/log helpers | Distinct protocol oracles and crash assertions retained | 已验证完成 — [Core regression](evidence/core-after-dependency-fix.json) |
| E03 | Private heartbeat enum | Queued/writing/early-response/awaiting-response behavior preserved | 已验证完成 — [heartbeat transitions](MAINTENANCE.md) |
| E04 | Private retry origins and state table | Both budgets and original resets preserved | 已验证完成 — [origin/reset table](../../FAILURE-MATRIX.md) |
| E05 | Existing diagnostics usability | Stats/error/delivery status guide; no public field additions | 已验证完成 — [existing diagnostics guide](../../DELIVERY-GUIDE.md) |
| E06 | Native benchmark matrix | PID/hash; 5s warmup, 30s samples, 3 repeats; IDs and outcomes | 已实现待验收 — [measurement method](../../PERFORMANCE.md); full runs pending |
| E07 | TCP and mTLS final soak | Each 30min/100 recoveries; live and post-scope resources | 已实现待验收 — TCP/mTLS short probes passed; final 30min/100 runs pending |
| F01 | ESP32 preparation and measurement protocol | Firmware/consumer/scripts/wiring/calibration; no guessed GPIO | 已验证完成 — [disabled firmware build](evidence/esp32-preparation.json), [six host windows](evidence/durable-six-boundaries-first.json) |
| F02 | B2-A HA controller live acceptance | UI ON/OFF, discovery/availability/feedback/query/no stale replay | 等待现场条件 |
| F03 | B2-B independent durable consumer live acceptance | Six host crash seams, packet/DUP/business IDs, physical outcomes | 等待现场条件 |
| F04 | B2 fault and evidence matrix | Duplicates/conflicts/expiry/retained/network/restarts/session/ACK delay | 已验证完成 — [cases and evidence format](../../../examples/esp32/README.md) |
| G01 | Freeze final candidate and difference report | Source/API/schema/dependencies/behavior hashes | 已实现待验收 — [API/schema/dependency comparison](evidence/api-schema-dependencies.json); final manifests pending |
| G02 | Pro review package | Complete ledger, reproducible evidence, unresolved decisions | 已实现待验收 — [Pro questions](PRO-DECISIONS.md), [release-note draft](CANDIDATE-RELEASE-NOTES.md); evidence bundle pending |
| P01 | CONNACK/TLS retry policy by dial source | Present evidence and alternatives; preserve current behavior | 等待 Pro 决策 |
| P02 | Stable-window budget resets/public parameters | Baseline/recovery evidence, no new defaults | 等待 Pro 决策 |
| P03 | Public API/test seam/diagnostics/recovery format promises | Inventory and compatibility options | 等待 Pro 决策 |
| P04 | Schema/inbox/QoS2/MQTT5/backends | Prioritized future research questions | 明确后续研究 |
| P05 | Performance thresholds/concurrency/durability tradeoffs | Measured baseline first | 等待 Pro 决策 |
| P06 | Independent adopters/user research | Repository-owned drivers are not external adoption | 等待 Pro 决策 |
| P07 | Release approval and permitted support claims | Candidate → approval → upload → fresh registry → assets | 等待 Pro 决策 |

## Execution boundaries

Keep the verified compiler/dependencies fixed, public API and SQLite schema 2
unchanged. Do not change refusal policy or budget resets while collecting
evidence. Do not merge, tag, publish or create a formal release. Prepare B2
without changing host networking or operating hardware until the wiring and
network are ready. Missing physical evidence remains missing; host-level logical
results cannot replace it. A blocked row does not stop independent work.

## Progress

- H01 fixed with original native/raw red/green evidence; fixed hosted Linux/macOS H01 acceptance passed.
- Fixtures, heartbeat enum, phase-policy tests, recovery documentation and isolated storage faults completed.
- Shared Core/Integration and clean first candidate/operator consumers passed locally. The first clean CI run of the shared entrypoint exposed a missing registry update; its first failures are retained, and `dependencies` now runs before build.
- Benchmark, live-PID soak, six exact host crash windows and separate B2 consumer are implemented. B2 software preparation is not B2 live acceptance.
- Final candidate/platform Release and review inventory remain in progress. No source merge, tag, registry upload, branch protection or hardware/network mutation has occurred.
