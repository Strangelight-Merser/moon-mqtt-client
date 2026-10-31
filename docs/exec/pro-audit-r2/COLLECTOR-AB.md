# WP1 collector control

An isolated detached worktree at `1f6c29f` used the current library source
for two native macOS runs. The stable run used that commit's blocking collector.
The control replaced only `examples/soak_driver/main.mbt` with the exact file
from the frozen `0bf9e31` candidate, including its 100 ms `next_event()`
timeout loop and original shutdown order. Neither tracked worktree nor frozen
candidate was modified for the control. Both used the same planned 30-second
schedule: five broker stops/restarts, four publishers, 128-byte payloads and
0.2-second downtime. They are separate executions with different actual
operation timing, so this does not isolate runtime timing perfectly.

| Observation | Blocking collector | Original 100 ms collector |
|---|---:|---:|
| Planned faults and completed recoveries | 5 / 5 | 5 / 5 |
| Final generation / reconnects | 6 / 5 | 6 / 5 |
| `stats.disconnects` / collector log / consumed JSON rows | 5 / 5 / 5 | 5 / 5 / 5 |
| Final event queue depth | 0 | 0 |
| Full current soak gate | Passed | Failed: original driver has no `collector_complete` or `shutdown_failed` proof fields |

The control did **not** reproduce an emit/consume difference. Its five events
were all consumed in this schedule. Its `final` row is emitted before the
client scope ends, and the original collector does not observe queue close;
therefore its zero queue depth at that point cannot prove post-shutdown drain.
The new collector waits for `Closed`, records `collector_complete=true`, and
its final row is after shutdown. This supports the changed observation
contract, not a claim that the old collector always lost events. The earlier
macOS failure remains a failure.

Local `_build/r2/collector-ab/` holds both exact driver sources, six raw files
per run and a SHA-256 manifest. The original driver source's Git provenance is
`0bf9e31:examples/soak_driver/main.mbt`; the stable source's provenance is
`1f6c29f:examples/soak_driver/main.mbt`. The full-control runner exited 1
because the original driver lacks the two new proof fields, as recorded in its
`summary.json`. No library defect is inferred from that exit code.
