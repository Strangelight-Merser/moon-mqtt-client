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
| Hosted Linux/macOS CI | Done | Linux and macOS passed for commit `fe2ef4c`; the fixed-toolchain check and the rolling-stable compatibility job are separate in `.github/workflows/check.yml`. Re-check the Actions page if the branch moves. |
| Pinned toolchain | Done, fail-closed caveat | `scripts/install-moonbit-ci.sh fixed` checks both the published and the downloaded SHA-256 against a recorded digest and verifies the exact `moon 0.1.20260904` version string. The official CDN only serves immutable-looking versioned paths as `latest` archives, so a new MoonBit release makes this job fail closed until the digest is re-recorded. That is deliberate; do not weaken it to a moving digest. |
| Mooncakes release and install test | Done | `Strangelight-Merser/moon-mqtt-client@0.2.0` published 2026-09-15T08:35:55Z; `moon publish` returned `200 OK` after re-checking the extracted package. `tests/consumer_smoke.py --registry` resolved `@0.2.0` in a clean temporary module and completed a QoS 1 round trip. Package sha256 `ee5af2a2…b76b`. |
| Push tag/branch, GitHub Release and asset checksums | Blocked locally | Mooncakes is published, but no Git credential is available in this environment (`could not read Username for 'https://github.com'`). Push `codex/mqtt-client` plus tag `v0.2.0`, then create the GitHub Release. Record any GitHub source-archive checksum in `docs/RELEASE-NOTES-v0.2.0.md`; the Mooncakes package checksum is already recorded there. |
| EMQX interoperability | Done | Pinned official `emqx/emqx:5.8.8` image (digests in `tests/emqx_interop.py`): QoS 1 both directions, subscription denial and restart recovery passed. |
| Soak test | Done | 30 minutes, 1 KiB QoS 1, concurrency 16, 100 disconnect/recovery cycles. All gates met: 10,828,581 acknowledged, 10,405,797 observer messages with 0 corrupt, `pending/business/control/event_queue/active_workers` all 0, 100/100 recoveries, exit 0. Recovery p95 0.351 s. Resource sampling was unavailable in this sandbox and is recorded as such. Evidence: `tests/integration/artifacts/soak-v0.2.0/summary.json`. |

## Competition submission

| Requirement | State | Action |
|---|---|---|
| Meaningful commits | 17 on `codex/mqtt-client` | Real fix/verification work only, no padding. The organizer's judgment of which commits count has not been obtained; treat the count as a submission fact, not an approval. |
| Registration and group join | Participant action | Not technically verifiable here. |
| Integrity declaration | Participant action | Must be signed by the participant. |
| Participant-written one-page statement | Participant action | `docs/FINDINGS.md`, `docs/SCENARIOS.md` and `docs/VALIDATION.md` provide the engineering basis; the statement itself must be written and understood by the participant. |
| Mooncakes publication requirement | Blocked on release | Re-read the charter's submission section before submitting; do not assume the requirement from earlier notes. |
| Main branch at v0.2.0 | Open (needs push access) | `main` is still at the 0.1.0 commit `9bd3813`; the v0.2.0 work is on `codex/mqtt-client` at `85bc0a5`. Decide the fast-forward/merge and push once a credential is available. |

## Before tagging v0.2.0

1. Run `./scripts/check.sh`, `examples/mqtt_demo/demo.py` and
   `tests/protocol_faults.py` on the frozen commit.
2. Confirm `docs/VALIDATION.md` numbers match that run and that no open gate is
   described as complete.
3. Decide how `codex/mqtt-client` reaches `main` and record it.
4. ~~Publish~~ Done: `@0.2.0` is on Mooncakes and the registry install test
   passed; the checksum and registry record are in `docs/RELEASE-NOTES-v0.2.0.md`.
5. Push `codex/mqtt-client` and tag `v0.2.0`, advance `main`, create the GitHub
   Release, and add any GitHub asset checksum to the release record.

Internal freeze target: 23 September (Beijing time), with 24 September reserved
for submission problems. If time runs short, defer the general CLI and optional
adapter work; the confirmed defects, fault regressions, state-recovery demo and
release acceptance are not optional.
