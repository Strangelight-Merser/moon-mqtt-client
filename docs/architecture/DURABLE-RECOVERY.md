# Durable logical-session recovery

Status: operator tooling. `resolve` and `resume` are **experimental**; that label
does not weaken data preservation, identity ownership or no-overwrite guarantees.
The implementation is an independent Python CLI
using the standard-library `sqlite3` module and pinned `paho-mqtt==2.1.0`. The
MoonBit public API, runtime, original tests, and durable schema version 2 stay
unchanged.

## Supported operation

B1 retires one complete durable logical session and creates an empty schema-2
outbox for a different client ID. It does not move, delete, or replay an old
row. The application may submit new business work later with new delivery IDs
after its own reconciliation.

An acknowledged row is absent from the old database, but absence does not
prove that a publish was never sent. Clearing broker session state also cannot
undo a retained publish, device action, or other physical side effect. The CLI
therefore has no resend, replay, per-row force-delete, same-ID, or overwrite
mode. Old IDs are evidence only and cannot be reused as proof of `NotSent`.

The operation is:

1. inspect or export an existing source without network or source writes;
2. publish a complete, checksum-verified resolution archive and `Prepared`
   sidecar journal;
3. transactionally retire the source identity while retaining every row;
4. clean the old and new broker client IDs through separately authorized
   handshake-only connections;
5. create and publish an empty schema-2 outbox for the different new client ID.

## Observation

Archives contain sensitive business payloads, topics, identifiers and operator
decisions. File mode 0600 restricts local access but does not encrypt contents.
SHA-256 detects byte changes only when compared with a trusted digest; it does
not authenticate the origin or prevent an attacker replacing both data and
hashes. Keep archives and credentials private. CI/public review bundles contain
synthetic fixtures and summaries, never raw user archives or device secrets.

Online resolution supports plain TCP only. A store bound to TLS/WS is rejected;
never change its identity or downgrade transport to make the CLI accept it.
Offline inspect/export do not require a broker connection.

```text
python scripts/durable_recovery.py inspect --source OLD
python scripts/durable_recovery.py export --source OLD --archive ARCHIVE
```

Both commands use a SQLite URI with `mode=ro`, `busy_timeout=0`, and a read
transaction. Missing paths are rejected by SQLite and cannot be created. The
source must have exactly the schema-2 metadata and outbox tables, one valid
metadata row, and a successful integrity check. Empty, unversioned, unknown,
corrupt, or busy stores fail closed. Export uses SQLite's consistent backup
API while the read transaction is held; its progress callback rejects
`BUSY`/`LOCKED` or an expired bounded deadline instead of sleeping forever.
It never opens the source through `SqliteOutbox::open` and never initializes or
migrates it.

Inspect emits the schema version, raw logical-session identity,
`known_session`, and all extant rows in sequence order. Each row includes the
exact payload and encoded properties, delivery ID, packet ID, topic, retain,
expiry fields, attempts, `ever_started`, state, and cause.

Export builds one container file beside the requested archive. It contains a
consistent schema-2 SQLite copy and canonical JSON manifest with the same raw
fields, SHA-256 hashes, and this explicit limitation: deleted rows cannot be
enumerated and absence is not `NotSent`. The complete temporary container is
flushed and published with a same-filesystem hard link, which cannot replace an
existing path, followed by a parent-directory flush. The published archive is
reopened and verified. Any later hash or content mismatch is a hard failure.

## Resolution request

```text
python scripts/durable_recovery.py resolve --request DECISION.json
python scripts/durable_recovery.py resume --request DECISION.json
python scripts/durable_recovery.py status --source OLD
```

The canonical request contains an operation ID; canonical source, archive, and
target paths; broker endpoint and protocol; distinct old and new client IDs;
and an operator decision. The decision includes operator, timestamp, rationale,
a same-authenticated-principal assertion, explicit ownership and cleanup
authorization for each client ID, and exactly one disposition for every extant
row: `ConfirmedApplied`, `ConfirmedNotApplied`, or `AbandonedUnknown`.
Dispositions are operator assertions, never library deductions. Credentials
may be read from named environment variables and are not serialized into the
archive or journal.

The CLI rejects missing authorization, identical IDs, path aliases (including
same inode), a non-absent target, an existing archive for a new operation, or a
request that differs from an existing journal. The same-principal statement is
the operator's assertion; MQTT CONNACK does not prove identity ownership.

Use the CLI in this order:

1. Run `inspect` and preserve its output. Run `export` if a standalone
   observation archive is needed.
2. Reconcile every extant row outside the CLI. Decide its audit disposition;
   do not infer `NotSent` from an absent row.
3. Copy and edit the request below. Verify both client IDs are exclusively
   owned for the whole operation and authorize each broker cleanup separately.
4. Run `resolve --request DECISION.json`. If it exits after `Prepared`, rerun
   `resume --request DECISION.json`; never edit the request or journal.
5. Run `status --source OLD` to verify `Complete`, then configure the
   application with the new outbox path and new client ID.

The following is an editable shape, not an operator decision or permission to
clean any real broker session:

```json
{
  "version": 1,
  "operation_id": "replace-with-unique-operation-id",
  "source": "/absolute/path/old.sqlite3",
  "archive": "/absolute/path/recovery.mqttrec",
  "target": "/absolute/path/new.sqlite3",
  "broker": {
    "host": "127.0.0.1",
    "port": 1883,
    "protocol": "mqtt311",
    "transport": "tcp",
    "tls": "plain",
    "timeout_seconds": 5
  },
  "old_session": {
    "client_id": "old-owned-id",
    "session_expiry_secs": 0
  },
  "new_session": {
    "client_id": "new-owned-id",
    "session_expiry_secs": 0
  },
  "decision": {
    "operator": "replace-with-operator",
    "decided_at": "2026-09-21T00:00:00Z",
    "rationale": "replace-with-reconciliation-basis",
    "same_principal_asserted": true,
    "old_client_id_owned": true,
    "old_cleanup_authorized": true,
    "new_client_id_owned": true,
    "new_cleanup_authorized": true,
    "records": [
      {
        "delivery_id": "replace-with-an-id-from-inspect",
        "disposition": "AbandonedUnknown"
      }
    ]
  }
}
```

For MQTT 5, use `"protocol": "mqtt5"` and a nonzero durable
`session_expiry_secs` for both logical sessions. Resolution deliberately
supports plain TCP only and rejects TLS or WebSocket requests. Offline
`inspect` and `export` still work on schema-2 databases whose stored identity
describes TLS or WebSocket because they perform no connection or identity
rewrite.

## Local commit order

Resolution opens the source with SQLite `mode=rw`, validates the existing
schema 2 before any write, sets zero busy timeout, and obtains retained
exclusive ownership using `locking_mode=EXCLUSIVE` and a completed exclusive
transaction. With no active write transaction, the same owned connection
takes the snapshot and backup. This mode allows SQLite to perform its normal
transaction-journal recovery, but the CLI never initializes, deletes, or
migrates an old schema.

While holding ownership it builds the complete resolution archive in staging.
The resolution manifest contains the observation data plus the canonical
request and operator decisions. It hashes and flushes every file and directory,
publishes the archive without replacement, and verifies it. If publication
succeeded but the process exited before creating `Prepared`, the same
operation/request and verified archive may continue; an unrelated existing
archive is rejected and an orphan staging file is never trusted or deleted.

Next it builds a separate SQLite journal in a temporary file. The single
versioned journal row contains the complete canonical request, archive locator
and hash, source fingerprint, old identity, target identity, and phase
`Prepared`. The database is committed, integrity-checked, flushed, and
published at the deterministic `<source>.recovery.sqlite3` path without
replacement. Only after the published journal is reopened and verified may
one source transaction change `durable_meta.identity` to
`moon-mqtt-retired-v1|<operation-id>|<archive-hash>`. It never changes or
deletes an outbox row and leaves schema version 2 unchanged.

The retired identity makes the current and older clients fail their existing
exact-identity check. A nonempty schema remains at the original path, so it
cannot be silently treated as a new database. The original identity and all
records remain in the verified archive. The sidecar is a cross-system journal,
not an extension of schema 2.
The source is a managed evidence file from `Prepared` onward. Moving or copying
an old database and opening that copy outside recovery violates the ownership
contract. The journal records the source device and inode so resume detects a
path replacement and refuses to retire a substitute while an original remains
usable elsewhere.

## Broker cleanup

The journal records `OldCleanRequested` before contacting the broker. A
dedicated Paho client then uses the old client ID with no Will and no
application callbacks. MQTT 3.1.1 sends Clean Session 1. MQTT 5 sends Clean
Start 1 with Session Expiry Interval 0. Only a successful CONNACK advances to
`OldCleanConfirmed`; timeout, refusal, disconnect, or process exit remains
resumable. A cleanup CONNACK with Session Present 1, or an MQTT 5 CONNACK with
a nonzero Session Expiry Interval, is rejected as inconsistent with the clean
handshake. Repeating the clean handshake is idempotent.

The new client ID follows the same independent sequence,
`NewCleanRequested` then `NewCleanConfirmed`, using its distinct explicit
ownership and cleanup authorization. This guarantees that the replacement ID
does not inherit an earlier broker session. Neither connection can PUBLISH,
SUBSCRIBE, or install a Will. An optional DISCONNECT after accepted CONNACK is
the only packet after CONNECT.

These settings follow [MQTT 3.1.1 section 3.1.2.4](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)
and the [MQTT 5 Clean Start and Session Expiry rules](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html).

The operator must keep both client IDs exclusively assigned to this recovery
from the first cleanup until the new application assumes ownership. MQTT has
no broker-side transaction spanning cleanup and the later new connection, so
the CLI cannot prevent an external process from racing either client ID.

## Replacement and resumption

After both confirmations, the CLI creates a complete schema-2 database in a
temporary file in the target directory. It uses the new durable identity,
`next_sequence=1`, `known_session=0`, and an empty outbox. It commits, checks
schema and integrity, flushes the file, and records `ReplacementReady` before
publishing. Publication uses a same-filesystem hard link, which fails if the
target exists, followed by directory flush and temporary-link removal. There
is never an observable final path containing an empty or partial file.

| Journal phase | Resume behavior |
| --- | --- |
| `Prepared` | Verify request, archive, source snapshot, and old identity; commit source tombstone. |
| `SourceRetired` | Record old cleanup intent. |
| `OldCleanRequested` | Repeat old-ID clean handshake until accepted CONNACK. |
| `OldCleanConfirmed` | Record new cleanup intent. |
| `NewCleanRequested` | Repeat new-ID clean handshake until accepted CONNACK. |
| `NewCleanConfirmed` | Build or validate the staged empty replacement. |
| `ReplacementReady` | Publish the exact staged replacement without overwrite. |
| `ReplacementPublished` | Verify source, archive, and target, then mark complete. |
| `Complete` | Verify and return the same result; perform no broker cleanup again. |

Every phase update is one durable SQLite transaction. Resume verifies the
canonical request, archive hash, source retired identity and target identity
before acting. An alias, altered archive, unexpected source identity, missing
staging file, existing mismatched target, or unknown phase fails closed.
If the source tombstone committed before the journal advanced from `Prepared`,
resume recognizes only this operation's tombstone and unchanged archived rows,
then advances. If target publication succeeded before `ReplacementPublished`,
resume accepts only the staged file's recorded identity, inode/content hash and
empty schema. A missing staging link is recoverable when the final target is
that exact published file; a pre-publication temporary file may be rebuilt.
After `ReplacementPublished` or `Complete`, the target may contain legitimate
new records, so resume checks its identity and schema rather than demanding the
old empty-file hash. `Complete` never repeats either broker cleanup.

## Minimum acceptance

- Inspect/export on a missing path creates nothing; observation leaves source
  bytes unchanged and fails busy against an exclusive owner.
- Archive verification detects changed database, manifest, request, decisions,
  or hashes and preserves exact payload/property bytes, IDs, causes, identity,
  and the deleted-row evidence limitation.
- `Prepared` exists durably before the source tombstone. Process-exit tests at
  seven named side-effect/phase windows resume to one result: archive before
  journal, journal before tombstone, tombstone before phase update, each
  accepted cleanup CONNACK before its confirmation phase, replacement build
  before publication, and target link before publication phase. Cleanup is
  repeated after either uncertain CONNACK; old rows are never deleted or sent.
- Raw peers see exactly two authorized cleanup sessions, each containing only
  CONNECT, accepted CONNACK, and optional DISCONNECT. A refused/missing CONNACK
  blocks the target; `Complete` produces no further connection.
- A native consumer rejects the retired source and opens the empty replacement
  normally with the new identity. Schema version remains 2 in both databases.
- Same ID, missing authorization, path alias, existing target, altered archive,
  mismatched request, corrupt source, and unsupported schema fail closed.
