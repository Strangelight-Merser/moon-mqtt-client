# WSS close investigation: bounded research record

## Preserved observations

- The first Linux EMQX WSS failure remains `OutcomeUnknown` with process exit 1.
  It is not relabeled as a pass.
- The first-round comparison ran 100 old and 100 candidate trials without
  reproducing the failure. Its report and broker logs remain under the old
  worktree's `_build/pro-audit/wss-linux-comparison/`.
- The first-round local raw mTLS/WSS peer-close probe is historical diagnostic
  evidence, not a controlled six-class race matrix or EMQX reproduction.

## Fixed experiment design

Two source versions: `f0a0e9ec33b7aee2aeee26ea20572904d05e3069` and
`0bf9e31bb53964682770af32ab9261468a106c43`. The first round is 20 trials
per class per version (120 per version). The classes are: peer close before
DISCONNECT admission; writer entered before complete frame; peer receives the
complete packet while the writer future is held; local write completed before
reader abort/pending settlement; WS Close/TLS close_notify/EOF (fixed 7/7/6
suballocation); and deadline/cancellation overlap (10/10). The six classes
need private, test-only barriers at write entry, write completion and pending
settlement. Record monotonic times, generation, pending slot zero, peer packet,
transport close source, first abort cause and worker join. Save experimental
patch and binary hashes separately from any release candidate.

Success means a completed local MQTT DISCONNECT write and controlled cleanup;
it does not mean a broker ACK. Before-write failure remains `NotSent` and a
possible partial write remains `OutcomeUnknown`. EOF and WS Close are not proof
of a completed MQTT write. A completed publish keeps its own result.

## Current status

An isolated test build inserted a 250 ms pause immediately after the local
DISCONNECT writer returned and before slot-zero settlement. A raw mTLS/WSS peer
first received the complete MQTT DISCONNECT. The test then observed the
test-build's write-return marker and only afterward sent WS Close, TLS
close_notify or transport EOF. Those three close sources had 20 trials each.
The synchronized marker observation preceded the peer close in every trial.

| Experimental build | WS Close | TLS close_notify | EOF | Source/binary SHA-256 |
|---|---:|---:|---:|---|
| Original pending classification + test pause | 20/20 `OutcomeUnknown` and exit 1 | 20/20 | 20/20 | `1e4f0b2b…` / `829afa7d…` |
| Private local-write-complete marker + same pause | 20/20 exit 0 | 20/20 | 20/20 | `6e06fdc4…` / `f1d28aff…` |

The full source copies, unredacted rows, peer timings, test harness and build
logs remain in `_build/r2/wss-experiment/`. They are experimental binaries,
not the candidate. The private fix marks successful local writer return before
reader abort can settle slot zero; `Pending::finish` still prevents a late
success from overwriting an earlier terminal result. A native unit test covers
local completion, possible partial write, no write, and first-result-wins.
The uninstrumented candidate also completed 60 raw mTLS/WSS peer-close trials,
but those trials alone do not force the race.

The fixed result identifies a reader-close race in the post-write settlement
window. It does not show that every historical `OutcomeUnknown` had that cause.
The other prescribed barrier classes (pre-admission, partial frame,
peer-complete/write-future-held, cancellation/deadline overlap) remain to be
executed. The current host has the Docker CLI but no Docker daemon; Linux
hosted CI is required for the unchanged pinned EMQX 5.8.8 interop assertion.
**WP2 remains open and Release HOLD** until those evidence gaps are reviewed.
