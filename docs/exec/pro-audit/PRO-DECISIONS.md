# Decisions for the next Pro review

The candidate implements H01 and behavior-preserving maintenance. It does not
change public API, schema 2, dependency versions or existing retry defaults.
The final evidence inventory, candidate manifest and per-platform Release report
are the review inputs; B2 physical results remain pending field conditions.

| Decision | Evidence now available | Suggested next decision / research |
|---|---|---|
| Initial, ordinary and maintenance negative CONNACK/TLS policy | Failure matrix and 18 typed/numeric phase cases, including maintenance TLS falling back to ordinary recovery | Decide terminal/retry tables by origin and error category before code changes. Do not parse MQTT311 display strings or confuse PUBACK rejection with dial rejection. |
| Budget reset semantics | Ordinary readiness resets attempts/delay; maintenance budget is lifetime; repeated recovery resources measured | Decide whether a stable window is needed, whether public parameters split, and the release in which defaults may change. |
| Public naming/testing/diagnostics | Generated API baseline unchanged; stats/handle guide; retained public test seams | Review names and removal/versioning of test-only public methods separately. New diagnostics should answer actual operator questions. |
| Recovery format promises | Separate operator ZIP, experimental resolve/resume, conservation/window tests | Decide compatibility/version guarantees and operator support before calling mutation tooling stable. |
| Schema and persistence growth | Schema 2 and transaction order unchanged; process/EIO/ENOSPC evidence | Define migration and identity-retirement policy before a new schema/backend; do not silently rebuild existing databases. |
| Inbox, QoS2, more MQTT5/backends | Outbox-only and protocol subset support matrix; no claim of downstream exactly-once | Validate demand and choose one bounded next capability. Inbox requires an application-transaction contract; QoS2 is separate protocol state and evidence work. |
| Performance thresholds and optimizations | 39 independent workload trials plus TCP/mTLS recovery soaks, IDs/PIDs/hashes and stage timing | Review platform variance and observer/trace costs before thresholds. Preserve fsync/COMMIT and queue bounds; any changed scheduling needs a separate contract decision. |
| External adoption and user research | Candidate consumers run outside source dependency resolution; all are repository-owned | Select independent users/projects and collect setup/debugging feedback. These test consumers are not independent adoption. No unsolicited outreach has been sent. |
| Release and public claims | Frozen source/package/operator manifests, Linux/macOS Release, explicit B2 gaps | Approve the candidate and exact version/support claims; then upload, fresh registry validation, and release/assets comparison. Preparation is not publication. |

Field prerequisites are separate from architectural decisions: confirm board
pins and low-voltage wiring, provide stable 2.4 GHz connectivity, and calibrate an
independent physical counter that resolves two applies. Then run B2-A and B2-B
with candidate/consumer/firmware binding. No need to redesign software merely
because that setup is unavailable. The existing hardware/network stays untouched
until those prerequisites and the user's selected timing are satisfied.

Stop the affected work package if a specification counterexample is found,
conservation fails, a fix needs a new public contract/schema, or measurements
cannot be made trustworthy. Preserve the minimal reproduction and alternative
choices; continue unrelated authorized work. Final release approval remains with
the release owner after this review.
