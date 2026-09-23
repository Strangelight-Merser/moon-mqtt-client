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

The six barrier-controlled classes have not run. The current host has the
Docker CLI but no Docker daemon, so the pinned EMQX 5.8.8 fixture cannot run
locally. Public CLI peer timing cannot independently hold the MoonBit writer
future or pending settlement, so repeating it would not resolve the key race.
No WSS runtime fix is justified by the available observations. **WP2 is open;
the candidate is Draft and Release HOLD.** Any next round should run the fixed
matrix first, then the unchanged Linux EMQX interop exit-zero assertion.
