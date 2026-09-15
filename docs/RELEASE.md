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
| Regression and fault coverage | Done | `docs/FINDINGS.md`; `docs/VALIDATION.md` records 26 unit, 11 integration, 5 fault, 4 demo results. |
| Warning-free build | Done | `moon check --target native` passes with no warnings. |
| License and attribution | Done | Apache-2.0, `NOTICE`, separate pinned codec and async dependencies. |
| Public repository | Done | https://github.com/Strangelight-Merser/moon-mqtt-client |
| Hosted Linux/macOS CI | Not re-run this round | The workflow exists and a prior run passed; a new run for the v0.2.0 commit has not been observed. Do not claim it as done until the Actions page shows it. |
| Mooncakes release and install test | Open gate | Release target `Strangelight-Merser/moon-mqtt-client@0.2.0`; run `tests/consumer_smoke.py --registry` from a clean directory after publishing and record the result. |
| Published asset checksums | Open gate | After publishing, compute and record the checksums of the release archives and link them from the release notes. |
| EMQX interoperability | Open gate | Mosquitto is verified; EMQX must pass the core send/receive, subscription-rejection and recovery cases before claiming broker interoperability. |
| Soak test | Open gate | 30 minutes, fixed 1 KiB, QoS 1, concurrency 16, 100 disconnect/recovery cycles; require pending count 0, drained queues, no FD leak and no dangling task. Results to `VALIDATION.md`. |

## Competition submission

| Requirement | State | Action |
|---|---|---|
| At least 10 meaningful commits | Not met yet | Only real implementation/test/review commits count; do not split one change artificially. |
| Registration and group join | Participant action | Not technically verifiable here. |
| Integrity declaration | Participant action | Must be signed by the participant. |
| Participant-written one-page statement | Participant action | `docs/FINDINGS.md`, `docs/SCENARIOS.md` and `docs/VALIDATION.md` provide the engineering basis; the statement itself must be written and understood by the participant. |
| Mooncakes publication requirement | Blocked on release | Re-read the charter's submission section before submitting; do not assume the requirement from earlier notes. |

## Before tagging v0.2.0

1. Run `./scripts/check.sh` and `examples/mqtt_demo/demo.py` on the frozen commit.
2. Confirm `docs/VALIDATION.md` numbers match that run and that no open gate is
   described as complete.
3. Tag and publish, then verify the published version from a clean directory:
   `moon add Strangelight-Merser/moon-mqtt-client@0.2.0` in a new module, run a
   QoS 1 publish/subscribe against a broker.
4. Record the release asset checksums and the registry install output in the
   release notes, linked from `VALIDATION.md`.

Internal freeze target: 23 September (Beijing time), with 24 September reserved
for submission problems. If time runs short, defer the general CLI and optional
adapter work; the confirmed defects, fault regressions, state-recovery demo and
release acceptance are not optional.
