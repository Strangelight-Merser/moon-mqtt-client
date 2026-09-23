# Software acceptance levels

`tests/scenarios.json` is the shared list consumed by local checks and GitHub
Actions. `./scripts/check.sh` defaults to **integration**, preserving the previous
full-software promise and adding formerly CI-only required suites.

| Level | Content |
|---|---|
| Core | Fixed native check/test/build, key raw peers including H01 and counter contract, MQTT5, maintenance, recoverable QoS1, short durable crashes, Mosquitto core, short TLS negatives and WS framing/close smoke |
| Integration | Core plus complete Mosquitto TLS/mTLS matrix, dial policy, source consumers, HA/transient, recovery administration, WS faults, Linux EMQX/WS/WSS |
| Release | Integration plus immutable candidate/operator consumers, hostile input, storage and recovery I/O faults, measurement validity, trace A/B, measured resources and final evidence inventory |

```sh
./scripts/check.sh core
./scripts/check.sh                    # integration
./scripts/check.sh release
./scripts/check.sh integration --list
```

Each invocation creates a new timestamped evidence directory; there is no hidden
retry. Commands, durations, logs, checksums, source files and results survive
failures. `--only` is for a diagnostic subset and never establishes a full-level
pass. Platform-not-applicable rows include a concrete reason. A required command
that cannot run fails the gate, rather than silently becoming a pass.

Pull requests run Core in `core (linux-x64)` and `core (macos-arm64)` jobs.
Pushes also run distinct Integration jobs. The existing weekly time and
explicit Release dispatch execute heavier acceptance; rolling compatibility
remains a separate job. Only the two Core contexts are proposed as required.
Rolling success does not replace the fixed compiler acceptance baseline. Required
platform jobs retain their names for the proposed branch rules.

Package preparation does not publish: `scripts/candidate.py` creates the real
Moon package and a separate versioned operator ZIP from one source snapshot.
`scripts/verify-candidate.py` verifies checksums and executes clean consumers.
No source checkout may substitute for the selected package. See
[current support/release status](CURRENT.md) and [operator installation](OPERATOR-INSTALL.md).

Resource evidence samples the native executable while alive. Post-scope cleanup
must be observed before process exit; a vanished PID is not evidence of stable
runtime resources. Measurement thresholds and tradeoffs are submitted to Pro
after collecting the baseline.
