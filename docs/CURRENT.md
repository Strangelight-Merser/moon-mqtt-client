# Current release and candidate status

| Item | Current state |
|---|---|
| Last public release | [v0.8.1](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.8.1), published 2026-10-31 |
| Source module version | `0.8.1`; published and verified |
| Fixed toolchain | `moonc 0.10.17+d971670cd`, `moon 0.1.20261023`; exceeds the competition minimum 0.10.16 |
| Software baseline | Tested `af6c9f6d`; merged/tagged `bcaecd6f`, identical tree; macOS uses Homebrew OpenSSL 3 |
| v0.8.1 release acceptance | Core, Integration and full Release passed on Linux/macOS at publication. See [0.8.1 acceptance](exec/release-v0.8.1/STATUS.md). |
| Publication | Mooncakes HTTP 200; clean registry TCP/mTLS plus 34 tests passed; all 9 GitHub attachments verified anonymously |
| Hardware | B2 physical output and independent execution counts remain unaccepted; excluded from this software release |
| Prior implementation audit | [2026-10-12 read-race/source-freeze fixes](exec/gap-repair/AUDIT_20261012.md) |
| Historical acceptance | Existing `docs/exec/STATE.md`, release notes and validation reports are historical snapshots |

## 2026-11-11 toolchain compatibility

Main revision `c7cb5fb` was checked against the official `latest` download.
`moon version --all` reported `moon` and `moonrun` `0.1.20261104`
(`940aa18`, 2026-11-04) and `moonc v0.10.20+65efd5b93` (2026-11-05).
See [MoonBit toolchain downloads](https://www.moonbitlang.com/download/).

- **Linux x86-64, native.** The November 9 rolling CI job passed `moon update`
  and native check, test, and build on this toolchain. [CI job](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/42629306153/job/129201150551)
- **macOS ARM64, native.** A clean checkout with the check fix passed
  `./scripts/check.sh integration` and all four MQTT state-sync demo scenarios.
  A fresh consumer installed `0.8.1` from Mooncakes and completed QoS 1 round
  trips over TCP and mTLS using Homebrew OpenSSL 3. The fixed 0.10.17 toolchain
  also passed `./scripts/check.sh core` with the fix.

The first macOS integration run passed its functional stages and failed the
source-identity check because `moon check` regenerated eight tracked `.mbti`
interfaces in the rolling compiler's format. The shared acceptance and rolling
CI checks now pass `--no-mbti`. API descriptor regeneration is explicit via
`moon info`.

Push and pull-request Core CI use the fixed compiler. The scheduled Linux job
checks the official rolling toolchain. In the separate scheduled Release jobs,
the trace A/B coefficient of variation exceeded the 15% limit. The
`evidence-integrity` stage failed with an `indeterminate` result on macOS on
November 2 and on Linux on November 9.
The functional stages on both affected jobs passed. [macOS job](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41453271663/job/125303493140), [Linux job](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/42629306153/job/129201151482)

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

To repeat the completed registry verification, run
`tests/registry_roadmap.py --version 0.8.1 --evidence <result.json>`
from the reviewed checkout. It removes `MOON_WORK`, creates a clean consumer and
installs the named registry dependency without local source substitution.
Prepublication `--package` results are explicitly a different evidence type.
