# Hosted macOS balanced trace comparison

This test-only branch starts at frozen `c52ba9b`. The protocol is committed
before the hosted run. It fixes the workload, five-second warmup, 30-second
sample, six balanced pairs, 15% population-CV interpretability gate and
20% throughput / 25% p99 investigation triggers. `run.py` rejects any source
change beyond this diagnostic directory and its workflow job.

The runner builds one native benchmark executable, records its hash, and uses
it for all twelve trials. It writes `summary.json` after each trial, including
partial or failed runs. Raw per-trial records and the diagnostic binary are
uploaded separately from the old Release artifact. The original three-pair
macOS Release E06 result remains indeterminate and failed regardless of this
supplement's outcome. This is trace off/on observation cost, not a comparison
between `0bf9e31` and `c52ba9b`.
