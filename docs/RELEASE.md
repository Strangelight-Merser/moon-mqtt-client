# Release and competition checklist (v0.2.0)

Passing local tests is separate from completing the competition's submission and
distribution requirements. The [official charter](https://bxup9uklfcb.feishu.cn/wiki/Dx4Bwd6D1i3GfHkajQCcF7SznEd)
was checked during the project decision on 2026-09-14; the organizer's final
notices govern acceptance. Unknown or unexecuted items are marked as such and are
not claimed as complete.

## Engineering and distribution

| Requirement | State | Evidence / remaining action |
|---|---|---|
| MoonBit main implementation | Done | Client runtime, contracts and demo processes are MoonBit; Python only provides independent tests. |
| Version and API | Done | `moon.mod` at 0.2.0; `pkg.generated.mbti` regenerated with `moon info`; contract in `docs/API-CONTRACT.md`. |
| Reusable scope | Done | Native MQTT 3.1.1 QoS 0/1 client; no application-specific topic coupling in the library. |
| Primary scenario | Done | `examples/mqtt_demo` + `demo.py`: 4 scenarios, simulated device, Paho observer, one command. |
| Three complete scenarios | Done | `docs/SCENARIOS.md`: state-sync thermostat, Frigate alert contract, ROS bridge contract. |
| Regression and fault coverage | Done | `docs/FINDINGS.md`; `docs/VALIDATION.md` records 26 unit, 12 integration, 10 fault, 4 demo, EMQX and soak results. |
| Warning-free build | Done | `moon check --target native` passes with no warnings. |
| License and attribution | Done | Apache-2.0, `NOTICE`, separate pinned codec and async dependencies. |
| Public repository | Done | https://github.com/Strangelight-Merser/moon-mqtt-client |
| Hosted Linux/macOS CI | Done | Both push and pull-request checks passed for `f07e201`, including the fixed installer; [PR CI](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/34950310413). PR #1 merged that tree into `main` at `a5ab5d7`. The optional rolling-stable job remains separate. |
| Pinned toolchain | Fixed version and checksums | `scripts/install-moonbit-ci.sh fixed` uses the official `0.10.12+1634b282e` version path, checks the recorded binary and core archive SHA-256 values, installs those verified bytes, and checks both `moon` and `moonc` versions. It does not depend on the moving `latest` alias. |
| Mooncakes release and install test | Done | `Strangelight-Merser/moon-mqtt-client@0.2.0` published 2026-09-15T08:35:55Z; `moon publish` returned `200 OK` after re-checking the extracted package. `tests/consumer_smoke.py --registry` resolved `@0.2.0` in a clean temporary module and completed a QoS 1 round trip. Package sha256 `ee5af2a2…b76b`. |
| Push tag/branch | Done | `codex/mqtt-client` and `v0.2.0` were pushed on 2026-09-15. The tag remains on the published tree `85bc0a5`. |
| GitHub Release and asset checksums | Done | [v0.2.0](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.2.0) was published on 2026-09-15 with the Mooncakes ZIP, tagged source archive and `SHA256SUMS`. All three attachments were downloaded without authentication; both archive hashes and bytes match the verified local files. |
| EMQX interoperability | Done | Pinned official `emqx/emqx:5.8.8` image (digests in `tests/emqx_interop.py`): QoS 1 both directions, subscription denial and restart recovery passed. |
| Soak test | Done | 30 minutes, 1 KiB QoS 1, concurrency 16, 100 disconnect/recovery cycles. All gates met: 10,828,581 acknowledged, 10,405,797 observer messages with 0 corrupt, `pending/business/control/event_queue/active_workers` all 0, 100/100 recoveries, exit 0. Recovery p95 0.351 s. Resource sampling was unavailable in this sandbox and is recorded as such. Evidence: `tests/integration/artifacts/soak-v0.2.0/summary.json`. |

## Competition submission

| Requirement | State | Action |
|---|---|---|
| Development commit history | Preserved on `main` | [PR #1](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/1) used a merge commit, preserving the 17 development commits recorded at `9a7815a` after `9bd3813`, plus the documentation and CI-installation update. Which commits qualify for the competition remains the organizer's decision. |
| Registration | Done (participant confirmed) | The participant confirmed registration on 2026-09-15. |
| Group join | Participant confirmation pending | Registration is confirmed; group membership has not been confirmed. |
| Integrity declaration | Participant action | Must be signed by the participant. |
| Participant-written one-page statement | Participant action | `docs/FINDINGS.md`, `docs/SCENARIOS.md` and `docs/VALIDATION.md` provide the engineering basis; the statement itself must be written and understood by the participant. |
| Mooncakes publication | Done | `Strangelight-Merser/moon-mqtt-client@0.2.0` is published and the registry install acceptance passed. Confirm the final submission requirements against the organizer's notices. |
| Main branch at v0.2.0 | Done | [PR #1](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/1) merged `codex/mqtt-client` into `main` at `a5ab5d7` on 2026-09-15. The release tag remains at the published tree `85bc0a5`; the default README is Chinese. |

## Completed release acceptance

1. Extracted the `v0.2.0` source archive into a clean temporary directory and
   passed `scripts/check.sh` plus all four state-sync demo scenarios.
2. Rechecked the Mooncakes install in a fresh consumer module with a QoS 1
   round trip; the package checksum matches the published index record.
3. Pushed the development branch and tag, passed Linux/macOS CI, and merged
   the branch into `main` without squashing its history.
4. Published the GitHub Release and downloaded all attached files again to
   verify their checksums and byte identity.
5. Recorded the release, archives and remaining participant actions in this
   checklist and `docs/RELEASE-NOTES-v0.2.0.md`.

Internal freeze target: 23 September (Beijing time), with 24 September reserved
for submission problems. Registration is confirmed; group membership, the
participant-written statement and the signed integrity declaration still need
participant confirmation.
