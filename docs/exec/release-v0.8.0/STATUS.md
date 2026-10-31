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
