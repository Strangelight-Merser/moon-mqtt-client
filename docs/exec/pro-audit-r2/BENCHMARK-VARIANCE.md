# WP3 platform variance review

The paired 13-workload × 3-trial matrix at `975e35a16d1b19e63afa15f20b4589b4018a344a`
used MoonBit `0.1.20260904`, five seconds of warmup and 30 seconds of sampling
per trial. The macOS and Linux summaries each contain 39 raw trial rows. These
are different hosted machines and operating systems; their absolute throughput
and latency numbers are not a cross-platform ranking or an external SLO.

| Platform | Repeated-trial variation within each platform | Trace on/off paired result |
|---|---|---|
| macOS 15.6 arm64 | Across the nine publish/durable workloads with nonzero throughput, the largest throughput population CV was 2.68% (`qos0-1k`); the other eight were at most 0.64%. `restore-100` completed 193–194 cycles, `restore-1000` 22–23, Receive Maximum 1 completed 9,654–9,726 flow cycles, and Receive Maximum 32 completed 16,256–16,475. | Median throughput drop 0.584%; median p99 change −2.034%; within the plan's 20%/25% investigation triggers. |
| Linux x86_64 Azure runner | Across the same nine nonzero-throughput workloads, the largest throughput population CV was 1.85% (`durable-stages`); the other eight were at most 0.73%. `restore-100` completed 256–259 cycles, `restore-1000` 30, Receive Maximum 1 completed 13,337–13,408 flow cycles, and Receive Maximum 32 completed 24,397–24,431. | Median throughput drop 0.725%; median p99 change −1.380%; within the same triggers. |

`receive-max-*` and `restore-*` record flow/restore cycles rather than a
publish-throughput value. The trace comparison is within each platform and
uses paired off/on trials; it does not show that instrumentation has zero cost.
The Linux Release workflow containing this benchmark failed later in the
`source_retirement` fixture, so its benchmark scenario passed but the Release
workflow did not. The final frozen source still requires its own full matrix.

Raw summaries: local macOS
`_build/r2/release-20260923T014608.982796Z-92179/benchmark-1790128144901312000/summary.json`;
downloaded Linux CI run
[`35807645417`](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35807645417),
`release-20260923T014633.563936Z-3652/benchmark-1790128390747718710/summary.json`.
