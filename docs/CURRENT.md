# Current release and candidate status

| Item | Current state |
|---|---|
| Last public release | [v0.8.0](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.8.0), published 2026-10-31 |
| Source module version | `0.8.1`; OpenSSL 3 fix candidate |
| Fixed toolchain | `moonc 0.10.17+d971670cd`, `moon 0.1.20261023`; exceeds the competition minimum 0.10.16 |
| Software baseline | 0.8.1 keeps the MQTT implementation and replaces macOS system LibreSSL with Homebrew OpenSSL 3 |
| Current acceptance | Fresh 0.8.1 validation pending; see [0.8.1 acceptance](exec/release-v0.8.1/STATUS.md) |
| Publication | 0.8.0 remains published with its post-release limitation recorded; 0.8.1 publication awaits fresh acceptance |
| Hardware | B2 physical output and independent execution counts remain unaccepted; excluded from this software release |
| Prior implementation audit | [2026-10-12 read-race/source-freeze fixes](exec/gap-repair/AUDIT_20261012.md) |
| Historical acceptance | Existing `docs/exec/STATE.md`, release notes and validation reports are historical snapshots |

## Support matrix

| Capability | Software acceptance scope | Claim boundary |
|---|---|---|
| Native MQTT311 / MQTT5 application subset, QoS0/1 | Pinned 0.10.17 compiler; Linux/macOS software checks passed | Not protocol certification or full MQTT5 |
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

After 0.8.1 is published, run
`tests/registry_roadmap.py --version 0.8.1 --evidence <result.json>`
from the reviewed checkout. It removes `MOON_WORK`, creates a clean consumer and
installs the named registry dependency without local source substitution.
Prepublication `--package` results are explicitly a different evidence type.
