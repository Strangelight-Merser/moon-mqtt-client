# Internal maintenance

The shared `fixture_session` initializes private state while callers retain
their own reader/writer, close ownership, capacities, packet IDs, reservation
maps and clocks. `test_pending` centralizes request defaults. The
[migration inventory](evidence/fixture-migration.json) preserves the names and
assertion counts of the former version-named regression files; the baseline
archive retains their original bytes. No protocol oracle or crash assertion
was removed. The migrated native suite passed all 156 tests.

Heartbeat uses a private `HeartbeatState` enum:

| Before | After | Transition |
|---|---|---|
| None | Idle | Queue one PINGREQ after idle interval |
| -1 | Queued | Ignore unsolicited PINGRESP; wait for write to begin |
| -2 | Writing | Accept early response without freeing heartbeat slot |
| -3 | RespondedWhileWriting | Finish exchange only after write completes |
| nonnegative timestamp | AwaitingResponse(timestamp) | Start response timeout from completed write |

Abort returns to Idle. An interrupted writer cannot revive an aborted exchange.
The same six heartbeat regressions and typed terminal-cause tests passed as part
of the 156-test suite after the representation change. This does not alter the
public API, clock source, timeout calculation, control budget or retry policy.
First compile diagnostics and the passing log remain in
`_build/pro-audit/heartbeat-enum*.log`.
