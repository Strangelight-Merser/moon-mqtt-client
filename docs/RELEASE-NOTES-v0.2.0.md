# v0.2.0 release record

Status: **published to Mooncakes and GitHub on 2026-09-15; merged into `main`.**
[PR #1](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/1) merged
the development history at `a5ab5d7` after Linux/macOS CI passed. The default
README is Chinese. The release tag remains at the published tree `85bc0a5`.

[GitHub Release](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.2.0)
was published at `2026-09-15T09:05:19Z`.

## Published artifact

| Field | Value |
|---|---|
| Package | `Strangelight-Merser/moon-mqtt-client@0.2.0` |
| Registry record | `.tools/moon/registry/index/user/Strangelight-Merser/moon-mqtt-client.index` |
| Published at | `2026-09-15T08:35:55.705527+00:00` |
| Package checksum (sha256) | `ee5af2a2503611a427a8555cdcb3cfaeff5ab4527bd4c85fadfe700998cbb76b` |
| Packaged zip | `_build/publish/Strangelight-Merser-moon-mqtt-client-0.2.0.zip` |
| Repository commit | `85bc0a5` (tag `v0.2.0`) |
| Registry install acceptance | `tests/consumer_smoke.py --registry`: fresh module in a clean temporary directory resolved `@0.2.0` from the registry, compiled, and completed a QoS 1 subscribe/publish/receive round trip against Mosquitto. |

`moon publish` re-checked the packaged zip in an extracted directory before the
server accepted it; the server returned `200 OK`. The registry record's
`checksum` equals the sha256 of the local zip, so the published bytes are the
bytes that were verified here.

## What is in v0.2.0

- **Runtime contract.** `operation_timeout_ms` replaces the ambiguous
  `ack_timeout_ms`; `write_timeout_ms` and `ping_response_timeout_ms` are
  separate budgets, all defaulting to 5000 ms. Protocol control packets use
  reserved bounded slots, so a saturated business queue cannot starve
  PUBACK/PINGREQ/DISCONNECT, and control-slot exhaustion terminates the
  connection instead of dropping a packet. PINGRESP is timed from the completed
  PINGREQ write. Reconnect uses bounded exponential backoff with per-client
  jitter (`Client::set_reconnect_seed` pins it for tests).
- **Diagnostics.** Read-only `Client::stats()`: generation, connection state,
  business/control/event occupancy, pending requests, reconnects, disconnects,
  unknown outcomes and the most recent disconnect reason. No credentials and no
  message bodies.
- **Correctness fixes.** A slow frame no longer loses bytes already read; a
  request timeout no longer poisons the caller's cancellation state;
  cancellation still reconnects; body reads use chunks instead of per-byte
  tasks; queue-occupancy mirrors reset on abort.
- **Contracts and examples.** Non-finite speeds and empty command ids are
  rejected; every emitted payload is JSON with quote/backslash/control-character
  escaping; an unknown initial relay state is `Uncertain` instead of an assumed
  OFF. A controller, an independent **simulated** device, and a Paho observer
  demonstrate desired/reported state synchronisation with four reproducible
  fault scenarios. A thin publish/subscribe CLI supports QoS, retain, TLS and
  `--stats`.

## Verification at publication time

| Check | Result |
|---|---|
| `moon check --target native` | Passed, no warnings |
| Unit tests | 26 passed |
| Mosquitto/Paho integration | 12 methods passed |
| Protocol fault injector | 10 cases passed |
| State-sync demo | 4 scenarios passed |
| EMQX (pinned 5.8.8 image) | send/receive, subscription denial, restart recovery passed |
| Soak | 1800 s, 100/100 recoveries, 10,828,581 acknowledged, 0 corrupt observer payloads, all final queues 0, exit 0 |
| Hosted CI | Linux and macOS passed for `fe2ef4c`; the published tree is `85bc0a5`, whose only later changes are test-harness and documentation files |

Details and evidence paths are in `docs/VALIDATION.md`.

## Compatibility

- Target: native (no browser or embedded target).
- MQTT 3.1.1, QoS 0 and 1, clean sessions only. No QoS 2, MQTT 5, persistent
  sessions, offline queue or client certificates.
- Verified fixed toolchain: `moon 0.1.20260904 (94521db 2026-09-04)`,
  `moonc v0.10.12+1634b282e` (2026-09-07).
- Verified peers: Mosquitto 2.0.22, Eclipse Paho Python 2.1.0, and the pinned
  official EMQX 5.8.8 image (digests in `tests/emqx_interop.py`).

## GitHub publication and acceptance

| Item | State |
|---|---|
| Push `codex/mqtt-client` and the `v0.2.0` tag | Done on 2026-09-15; the tag points to `85bc0a5`. |
| Advance `main` to the v0.2.0 tree | Done via PR #1, merge commit `a5ab5d7`; development commits preserved. |
| GitHub Release | Published with the verified package ZIP, tagged source archive and `SHA256SUMS`. |
| GitHub asset verification | Downloaded all three attachments from the public URLs without authentication; the checksum file and both archives are byte-identical to the verified local files. |
| Fresh source acceptance | Extracted the tagged source archive in a clean temporary directory; 26 unit, 12 integration, 10 fault cases, scenario smoke, separate consumer and all four demo scenarios passed. |
| Fresh registry acceptance | A new temporary module installed `@0.2.0` from Mooncakes and completed a QoS 1 round trip. |
| CI after installation repair | Linux and macOS passed on `f07e201`: [push](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/34950273493), [PR](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/34950310413). The merged tree `a5ab5d7` is identical. |

### Attached file checksums

```text
ee5af2a2503611a427a8555cdcb3cfaeff5ab4527bd4c85fadfe700998cbb76b  Strangelight-Merser-moon-mqtt-client-0.2.0.zip
0c0dbfb24328d803695d53f139a25cde017af835c49c24c959f92b95a9ee71f7  moon-mqtt-client-0.2.0-source.tar.gz
```

The source archive and Mooncakes ZIP preserve the release-time snapshot. Later
changes on `main` update documentation and CI installation only; the library,
examples and test sources remain identical to `v0.2.0`. The tag's original CI
run failed before tests because its installer used the moving `latest` URL.
The repaired installer uses the official `0.10.12+1634b282e` binary and core
archives with recorded SHA-256 values and exact version checks (FINDINGS D11).

The original 30-minute soak and EMQX evidence remains valid for that unchanged
source. Neither was rerun during release closeout, and the missing RSS/FD
sampling evidence remains a limitation. Competition registration is confirmed
by the participant; other participant actions remain in `docs/RELEASE.md`.
