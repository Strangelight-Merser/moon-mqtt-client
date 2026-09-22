# Current release and candidate status

| Item | Current state |
|---|---|
| Last public release | [v0.7.1](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.7.1), published 2026-09-19 |
| Source module version | `0.7.1`; source includes unreleased changes |
| Audit baseline | `f0a0e9ec33b7aee2aeee26ea20572904d05e3069` |
| Review candidate | Local `0.7.1-audit-<source digest>` assets; not a new registry version |
| Candidate manifest | Generated `_build/candidate/<id>/candidate.json` binds exact source files and ZIP checksums |
| Formal next version/release | Awaiting Pro/release-owner decision; no tag, upload or release in this implementation |
| Current task and evidence | [Pro implementation ledger](exec/pro-audit/README.md) |
| Historical acceptance | Existing `docs/exec/STATE.md`, release notes and validation reports are historical snapshots |

## Support matrix

| Capability | Software acceptance scope | Claim boundary |
|---|---|---|
| Native MQTT311 / MQTT5 application subset, QoS0/1 | Fixed compiler on Linux/macOS; raw peers and Mosquitto | Not protocol certification or full MQTT5 |
| TCP / TLS / mTLS | Linux/macOS, positive and negative certificate matrix | No insecure-verification option; identity files checked on each dial |
| WS framing | Linux/macOS independent raw peers | Native binary frames only |
| WS/WSS / EMQX interoperation | Pinned Linux Docker images | Hosted macOS has no Docker broker service; explicitly not applicable there |
| Recoverable delivery | ResumeSession, same scope and logical identity | QoS1, bounded admission, no offline queue |
| Durable outbox | SQLite schema 2, send direction, process crash tests | No durable inbox, device exactly-once, schema migration or physical power-loss claim |
| Recovery operator ZIP | Clean Python installation and native reopened candidate consumers | `resolve`/`resume` experimental; online plain TCP only |
| Maintenance reconnect | Opt-in 0x89/0x8B only | Ordinary policy and separate maintenance budget preserved |
| HA / ESP32 | Host simulation and prepared live acceptance | B2-A/B2-B physical acceptance waits for wiring/network and independent counts |
| Other targets/features | No new support claim | Browser/MCU MoonBit, QoS2, additional backends and inbox remain future decisions |

Do not copy a previous pass to a later candidate. Each run records commit, dirty
source hashes, compiler, platform and exact command. Final Release acceptance
must bind the frozen source and candidate ZIPs on both software platforms.

After approval and actual registry upload, run
`tests/registry_roadmap.py --version <published-version> --evidence <result.json>`
from the reviewed checkout. It removes `MOON_WORK`, creates a clean consumer and
installs the named registry dependency without local source substitution.
Prepublication `--package` results are explicitly a different evidence type.
