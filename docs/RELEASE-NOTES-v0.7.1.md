# v0.7.1

Published on Mooncakes and GitHub. This maintenance release covers audit tasks A0–A4.

- Prepare an in-memory recoverable delivery's MQTT 5 packet before marking its first possible write. Expiry before any writer call remains `TerminalNotSent`; a delivery already written by an earlier connection remains `TerminalOutcomeUnknown`. Durable possible-write persistence still commits before a network write.
- Keep one heartbeat pending while queued, writing, or awaiting PINGRESP. An early response stays accounted for until write completion; teardown clears the exchange. Responses received while the ping is only queued cannot satisfy its future write. Response timeout starts after write completion. Queue capacities and configured timeouts are unchanged.
- Keep the HA host controller alive through transient metadata publication failures. Discovery, availability, and the latest reported state occupy three bounded dirty slots. Reconnection queries the device again; physical commands are never queued for replay. Permanent errors still terminate the scope. Confirmed state publishes before online availability.

Public API and SQLite schema 2 are unchanged. Existing databases are not migrated or recreated. This release adds no durable recovery administration, reason-aware reconnect policy, or offline admission API.

Native white-box tests and actual native-controller fault tests cover the reported failures. Host simulation does not establish real Home Assistant or ESP32 acceptance. Release verification and exact evidence are recorded in `docs/exec/STATE.md` and `docs/exec/TASKS.md`.

Linux/macOS CI, clean registry TCP/mTLS, the 26-case registry roadmap and four registry HA fault cases passed. Public release assets were anonymously downloaded and byte-verified. Actual HA/ESP32 verification remains pending hardware.
