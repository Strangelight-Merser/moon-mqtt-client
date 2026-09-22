# Pro audit implementation

This is the current implementation record, separate from historical acceptance
reports. The approved scope is the full Pro audit and revised sections 6–16,
including the supplied H01 addendum. Later corrections take precedence.

Baseline: `f0a0e9ec33b7aee2aeee26ea20572904d05e3069`, tree
`71f79cb5e42a2e93fbf63e3a2a84bde5457d074d`. Work branch:
`codex/pro-audit-implementation`. Original working trees are preserved.

The original addendum ZIP SHA-256 is
`50659953356b9195b1f1f5297d0d707e4eb5cfd7c9e75b54d05501b1369880d5`.
Baseline source, inputs, command logs and environment manifests are retained in
`_build/pro-audit/`. These are local evidence, not published assets.

## Requirements and acceptance ledger

An empty status means no completion classification has been assigned. Allowed
nonempty statuses are **已验证完成**, **已实现待验收**, **等待现场条件**,
**等待 Pro 决策**, and **明确后续研究**. A source file or a test definition alone
does not establish acceptance. Each completed row must link execution evidence.

| ID | Requirement / batch | Acceptance | Status / evidence |
|---|---|---|---|
| A01 | Snapshot main/tree, originals, tools, dependencies, brokers | Hashes and baseline archive | |
| A02 | Full requirements traceability and evidence inventory | Every recommendation classified | |
| H01 | Native production reproduction; bounded deferred business buffer | Original assertion red then green; no API/schema change | |
| H02 | Public API Receive Maximum=1 peer | Admission barriers; SUB/UNSUB pass; PUBLISH stays blocked; no credit from SUBACK/UNSUBACK | |
| H03 | Capacity, FIFO, control priority, cancel/close/generation | Targeted invariants and existing regressions | |
| C01 | Failure matrix by dial source and operation stage | Typed causes/numeric reasons; table-driven current-policy tests | |
| C02 | TLS negative cases | CA, hostname, client certificate, key mismatch | |
| C03 | Hostile frames and bounded resources | Length/properties/flood/partial frame/reconnect/reason text | |
| C04 | Recovery conservation and crash windows | Archive/retirement/no-overwrite/journal/native old-new checks | |
| C05 | Storage failures | Isolated ENOSPC and child syscall fsync/EIO injection | |
| C06 | Recovery support/security documentation | Experimental mutation, TCP scope, sensitive archives | |
| C07 | User delivery guide and failure semantics | Ordinary/recoverable/durable; admission/ACK/cancel; no inbox promise | |
| D01 | Shared Core/Integration/Release scenarios | Local and CI use one list; default check stays Integration | |
| D02 | Candidate library consumers | H01 and maintenance driver compile/run from unpacked dependency | |
| D03 | Versioned operator ZIP | Clean installation, all commands, docs/dependencies/checksums | |
| D04 | Post-publication registry program | No MOON_WORK/source substitution; actual execution after publication only | |
| D05 | Current release/candidate/support status | One current status record; historical reports unchanged | |
| D06 | Branch governance proposal | Read rules; prepare PR/check/force-push/delete protections | |
| D07 | Exact candidate Linux/macOS Release | First failures, retries, SHA/platform/broker logs retained | |
| E01 | White-box fixtures and feature test grouping | Original assertions retained, baseline diff | |
| E02 | Small process/broker/port/log helpers | Distinct protocol oracles and crash assertions retained | |
| E03 | Private heartbeat enum | Queued/writing/early-response/awaiting-response behavior preserved | |
| E04 | Private retry origins and state table | Both budgets and original resets preserved | |
| E05 | Existing diagnostics usability | Stats/error/delivery status guide; no public field additions | |
| E06 | Native benchmark matrix | PID/hash; 5s warmup, 30s samples, 3 repeats; IDs and outcomes | |
| E07 | TCP and mTLS final soak | Each 30min/100 recoveries; live and post-scope resources | |
| F01 | ESP32 preparation and measurement protocol | Firmware/consumer/scripts/wiring/calibration; no guessed GPIO | |
| F02 | B2-A HA controller live acceptance | UI ON/OFF, discovery/availability/feedback/query/no stale replay | 等待现场条件 |
| F03 | B2-B independent durable consumer live acceptance | Six host crash seams, packet/DUP/business IDs, physical outcomes | 等待现场条件 |
| F04 | B2 fault and evidence matrix | Duplicates/conflicts/expiry/retained/network/restarts/session/ACK delay | |
| G01 | Freeze final candidate and difference report | Source/API/schema/dependencies/behavior hashes | |
| G02 | Pro review package | Complete ledger, reproducible evidence, unresolved decisions | |
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

- Baseline checkout created. Original H01 draft copied from the verified input
  for compilation and production-path reproduction before implementation.
