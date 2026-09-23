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

The original design named two source versions,
`f0a0e9ec33b7aee2aeee26ea20572904d05e3069` and
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
| Fixed runtime, test-only hold *inside* WS writer before return | 20/20 `OutcomeUnknown` and exit 1 | 20/20 | 20/20 | `79c69b20…` / `c218a031…` |

The full source copies, unredacted rows, peer timings, test harness and build
logs remain in `_build/r2/wss-experiment/`. They are experimental binaries,
not the candidate. The private fix marks successful local writer return before
reader abort can settle slot zero; `Pending::finish` still prevents a late
success from overwriting an earlier terminal result. A native unit test covers
local completion, possible partial write, no write, and first-result-wins.
The uninstrumented candidate also completed 60 raw mTLS/WSS peer-close trials,
but those trials alone do not force the race.
For the held-writer control, the peer had the entire MQTT DISCONNECT while the
local `WebSocketWriter::write_once` call was still held. The private completion
marker remained false, so all 60 outcomes stayed unknown. Each synchronized
peer close followed the test marker. This confirms that peer receipt alone does
not promote a possible partial local write to success.

The fixed result identifies a reader-close race in the post-write settlement
window. It does not show that every historical `OutcomeUnknown` had that cause.

## Later local barrier runs (not yet independently reviewed)

The actual local barrier runs used isolated test-hook variants of the newer
candidate, toggling the private completion fix. They are not direct runs of the
two commits named in the original design. Raw records and test-only source
copies are preserved under `_build/r2/wss-experiment/`. The local
`_build/r2/wss-local-evidence-975e35a.zip` (SHA-256
`75580d7b22734f561d27b4563430d2676a3eeb9a0111dada00b14e99879de559`)
contains the JSON/logs, probe scripts and source overrides relative to named
Git bases. It is local review evidence, not a published release asset. Each row
below names JSON files in that directory; they carry source and binary hashes,
trial outputs, markers, and peer-close timestamps.

| Barrier | Original behavior | Private-fix behavior | Interpretation |
|---|---|---|---|
| Before DISCONNECT admission (`pre-admission-*.json`) | 20/20 `NotSent` | 20/20 `NotSent` | Seven WS Close, seven TLS close_notify and six EOF trials per build. No write was admitted. |
| Incomplete WS frame (`partial-*.json`) | 20/20 `OutcomeUnknown` | 13/20 `OutcomeUnknown`; 7/20 local success | The test writes four bytes of the masked WS frame, waits 250 ms, then attempts the rest. In the seven successes the second local write returned despite the earlier peer close. Peer receipt of the full MQTT packet was not established, and these are not broker-execution successes. |
| Completed local write before peer close (`before-summary.json`, `fixed-summary.json`) | 60/60 `OutcomeUnknown` | 60/60 local success | Twenty trials each for WS Close, TLS close_notify and EOF; the peer had the complete DISCONNECT before its close. |
| Full packet at peer while local writer is held (`held-writer-summary.json`) | — | 60/60 `OutcomeUnknown` | Peer receipt alone did not mark the local write complete. |
| 100 ms deadline overlap (`cancel-deadline-*.json`) | 10/10 `OutcomeUnknown` | 10/10 local success | Only one trial per build recorded the actual deadline branch; the other trials establish nearby peer-close timing, not ten forced timeouts. |
| Scope cancellation overlap (`cancel-scope-*.json`) | Seven `OutcomeUnknown`, three cancellation completions | No `OutcomeUnknown`: eight cancellation completions, two ordinary completions | Process exit zero is the probe oracle, not ten MQTT write successes. The first, overly strict fixed-build oracle failure remains saved separately. |

The initial Linux EMQX WSS failure remains a failed observation. The pinned
Linux EMQX WS/WSS assertion passed in the later `975e35a` Release run, while
that run failed at an unrelated recovery I/O window. This positive run does
not identify the cause of the initial WSS failure. The local barrier records
need independent timing and result review, and the final candidate still needs
exact-source cross-platform acceptance. **WP2 remains open and Release HOLD.**
