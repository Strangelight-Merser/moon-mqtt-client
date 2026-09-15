# v0.2.0 release notes (draft, pending publication)

Status: **not yet published.** This is the prepared release record. Do not publish
it as evidence until the archive checksums and the registry install output below
are filled in from the actual publication.

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
- **Correctness fixes.** A slow frame no longer loses bytes already read (the
  parser keeps its partial-frame state across idle slices); a request timeout no
  longer poisons the caller's cancellation state; cancellation still reconnects;
  body reads use chunks rather than per-byte tasks; queue-occupancy mirrors reset
  on abort.
- **Contracts and examples.** Non-finite speeds and empty command ids are
  rejected; every emitted payload is JSON with quote/backslash/control-character
  escaping; an unknown initial relay state is `Uncertain` instead of an assumed
  OFF. A controller, an independent **simulated** device, and a Paho observer
  demonstrate desired/reported state synchronisation with four reproducible
  fault scenarios. A thin publish/subscribe CLI supports QoS, retain, TLS and
  `--stats`.
- **Verification.** See `docs/VALIDATION.md` for the executed results and the
  soak run recorded in this release.

## Compatibility

- Target: native (no browser or embedded target).
- MQTT 3.1.1, QoS 0 and 1, clean sessions only. No QoS 2, MQTT 5, persistent
  sessions, offline queue or client certificates.
- Verified fixed toolchain: `moon 0.1.20260904 (94521db 2026-09-04)`,
  `moonc v0.10.12+1634b282e (2026-09-07)`.
- Verified peers: Mosquitto 2.0.22, Eclipse Paho Python 2.1.0, and the pinned
  official EMQX 5.8.8 image (see `tests/emqx_interop.py` for the digests).

## Open items before publication

| Item | State |
|---|---|
| Final `main` tag and merge | Not done; `main` is still at the 0.1.0 commit. |
| Archive checksums | Not computed; fill in after publishing. |
| Mooncakes publish and clean-directory install | Not run for 0.2.0. |
| GitHub Release notes | Not created. |
