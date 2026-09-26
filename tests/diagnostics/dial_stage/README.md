# Isolated R2 dial-stage run

This branch starts from frozen candidate `c52ba9b`. It adds private diagnostic
markers to `runtime.mbt`, a selected Mosquitto connection/CONNACK log collector,
and a workflow-dispatch-only macOS job. It is never a release candidate.

The job first builds the native binary, runs short TCP/mTLS broker-restart
calibrations, then checks five peer-controlled negative boundaries: numeric
CONNACK 3, EOF before CONNACK, TCP failure, bad TLS trust and TLS handshake
timeout. Only after those pass does it run one 1800-second, 100-restart TCP soak
and one matching mTLS soak. The fixed protocol, exact Git diff, run state,
binary identities and raw logs are retained in `_build/dial-stage-hosted`.

`build_ledger.py` joins process-wide attempt IDs to socket source ports and
broker restart instances. A ready dial must match broker MQTT identity and
numeric CONNACK 0; every emitted Disconnected marker must match an event in
generation and order. Ambiguity fails the diagnostic. A pre-TCP failure has
no socket or broker association and remains explicitly unmapped.

The artifact upload includes named evidence files only. Test PKI private keys,
certificate files and SQLite stores are excluded. The new hosted results can
classify only failures that occur in this diagnostic run; they cannot assign a
cause to old artifacts, measure release-binary performance or lift Release HOLD.
