# 0.8.0 software release acceptance

User authorization on 2026-10-31 covers the previously proposed toolchain upgrade,
final software integration, documentation, GitHub/Mooncakes publication and clean
registry consumer verification. The nine supplied competition requirements govern
this delivery. Physical B2 remains a separate, unaccepted scope; prior hardware
HOLD records are retained as historical evidence.

Working branch: `codex/competition-release-v0.8.0` in the existing
`moon-mqtt-release-record` worktree. Baseline: `8933416`; no hardware-operation
commits are included. Fixed compiler: `0.10.17+d971670cd`.

Current status: preparing and validating the release; not yet published.
Evidence is retained under `_build/release-v0.8.0/` and each timestamped
`_build/acceptance/` invocation. No historical pass is assigned to this new build.
Original tests, scenario list, budgets and historical evidence remain unchanged.

Completion requires fixed-toolchain checks/build/tests, independent broker and
package consumers, current CI, a versioned registry upload, and clean registry
installation with runnable examples. Every publication result will be recorded.

## First-attempt finding

`f5ff954` passed every Core scenario on Linux and macOS, but correctly failed the
source-identity gate. MoonBit 0.10.17 writes generated API descriptors during the
build: seven tracked descriptors use the explicit `Int32` spelling instead of
`Int`, and four executable packages lacked descriptors. Local reproduction and
the downloaded Linux source/source-end manifests identify exactly the same
files. No test assertions or implementation changed. The generated descriptors
are committed before restarting acceptance; the source-freeze gate is unchanged.

First push run: [41205820711](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41205820711).
Its failed status and original artifacts are retained. The first full Release
run was canceled after this invalid source snapshot was identified, before
spending the long-soak budget. Local initial index download interruption is
recorded separately; it did not execute code tests.

## Cold compiler startup diagnosis

The original macOS Integration and push Core at `aa38a7f` timed out at the first
raw-protocol `moon run` invocation (8 seconds); the same-source PR Core passed.
The new compiler's `moon build` uses `-g -O0`, while `moon run` uses `-O0
-stacktrace`, invalidating the shared dependency build cache. An isolated hosted
[diagnostic](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41206987665)
reproduced a cold 8-second timeout, retained its captured stderr, then passed all
10 original cases in 3.03 seconds after an explicit `moon run --build-only`.
The diagnostic is not a replacement acceptance run. Original failed runs remain.

The acceptance runner now records a required build-only preparation before the
raw protocol suite. Original test files, 5-second peer accept deadline, 8-second
process deadline, and assertions remain unchanged. This separates compilation
from protocol timing rather than increasing the budget. The temporary diagnostic
workflow is removed after retaining its run and result.
