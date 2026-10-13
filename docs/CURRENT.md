# Current release and candidate status

| Item | Current state |
|---|---|
| Last public release | [v0.7.1](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.7.1), published 2026-09-19 |
| Source module version | `0.7.1`; source includes unreleased changes |
| Audit baseline | `f0a0e9ec33b7aee2aeee26ea20572904d05e3069` |
| Review candidate | Local `0.7.1-audit-<commit>-<source digest>` assets; not a new registry version |
| Candidate manifest | Generated `_build/candidate/<id>/candidate.json` binds exact source files and ZIP checksums |
| Formal next version/release | Release HOLD; complete remaining evidence gates before any merge, tag or publication |
| Current software acceptance | [2026-10-13 software closure](exec/gap-repair/SOFTWARE_ACCEPTANCE_20261013.md): frozen candidate `978966c` passed original Linux/macOS Release and E06; external TCP/mTLS per-dial evidence complete |
| Evidence tooling | `f5d00f5`: strict socket lifetimes, first TCP completion, shared clock and connect-entry capture; 14 regressions and 24 calibration cases per platform passed; does not inherit candidate Release PASS |
| Remaining release gate | B2 physical output and independent execution counts; software state does not establish physical effects |
| Prior implementation audit | [2026-10-12 read-race/source-freeze fixes](exec/gap-repair/AUDIT_20261012.md) |
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
