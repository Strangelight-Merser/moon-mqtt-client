# Single-backend durable outbox decision

Status: planned; SQLite seam verified, integration depends on Q1 acceptance. This decision scopes the beta, not a generic persistence framework. Root owns final approval after reviewing the Q1 implementation seams.

## Backend and ownership

Evaluate the pinned `moonbit-community/sqlite3@0.2.2` native binding rather than write a storage engine or custom C FFI. The isolated fixed-toolchain probe compiled and ran transaction commit/reopen/exact BLOB/rollback with the project's async0.21.3. This is a binding seam result, not outbox acceptance. Prefer its asynchronous open/prepare/step operations so filesystem I/O does not block the MQTT event loop; finalize every statement and close the database only after submitted jobs settle.

Use one SQLite file, one owning client scope, one concrete schema and explicit schema version. Bind metadata to the logical broker/client identity; reopening the same file for another identity fails before network delivery. Do not persist passwords/private keys. Reject concurrent ownership. Unknown schema, failed integrity checks and corrupt/non-database files fail closed; never delete or silently recreate them.

## Durability boundary

The explicit durable API admits a stable caller-supplied DeliveryId only after its record is committed. The ordinary publish and in-memory delivery APIs keep their contracts. A record includes topic, bytes, retain, packet ID, insertion order, immutable expiry, whether any write could have started, and a bounded attempt count. Persist possible-write state before touching the network. Remove an acknowledged record transactionally before reporting durable completion.

The unavoidable duplicate window is PUBACK received but acknowledgement removal not committed. A crash or storage error in this window leaves the same delivery eligible for protocol retransmission within its retained logical session. Never claim exactly-once or device execution. Reusing an ID after its acknowledged record is removed is not deduplicated indefinitely; applications own business IDs and receipts.

On restart, recover pending records in order with their packet identifiers and DUP history. If any could already have been sent, automatic retransmission requires the broker's retained logical session. Session loss must quarantine those records for application reconciliation, not silently publish them in a replacement session. Never implement recovery as repeated calls to ordinary publish. Records proven never started may be sent for the first time under an explicitly established new session.

Normal scope closure settles current waiters honestly but preserves durable unresolved records for the next explicit durable scope. Protocol errors, exhausted attempt budgets and session loss preserve an actionable blocked status and cause rather than erase evidence or replay without consent. Any reset/discard operation must be explicit, outside an active delivery scope, and documented as a business decision.

## Bounds and expiry

Keep the beta's admission bound aligned with the client delivery capacity; a general disconnected/offline bulk queue is not required. Bound active/blocked record count, total payload bytes and SQLite main-file pages. Prefer rollback journaling with a durable synchronous policy and a bounded single-record transaction; account for journal overhead instead of calling the main-file limit a total-disk limit. Reuse free pages. Do not accumulate unbounded acknowledged tombstones.

Expiry is an absolute application deadline, persisted once and checked before admission and replay. Never-started expired work can be removed as not sent. A possibly-sent MQTT3.1.1 delivery cannot be safely recalled: stop/quarantine it as an unknown outcome requiring reconciliation, without transmitting a stale physical command or releasing an identifier into an active unresolved protocol exchange. Wall-clock adjustment limitations are explicit. MQTT5 Message Expiry later complements, rather than retroactively changes, this contract.

Disk full, read-only, busy and corruption have distinct store errors. Admission failure produces no success handle and no network send. Failure to persist a pre-write transition must prevent that write. Failure after a network side effect stops delivery processing and preserves an unknown durable outcome. Storage cancellation waits for the binding's submitted operation to settle; a timeout is not evidence that a COMMIT did not occur. Callers can reopen/inspect the stable ID to reconcile uncertain admission.

## Acceptance

- Native store tests cover atomic admission, bounded records/bytes/pages, exact payload restart, rollback, expiry, ownership and identity mismatch.
- Deterministic process crash before send, after send/before PUBACK, and after PUBACK/before durable removal; independently inspect SQLite and broker wire identity/DUP.
- Real broker restart/reconnect and process-restart recovery using the same stable ID and retained session; session loss never causes blind replay.
- Deterministic SQLite full/read-only/corrupt inputs with no discarded original files, no send after failed persistence, and resource cleanup.
- Original transport and Q1 behavior stays verified on the integrated head; API/docs distinguish local transaction tests from actual broker/process evidence.

Sources: [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html), [SQLite PRAGMA reference](https://www.sqlite.org/pragma.html), [binding repository](https://github.com/moonbit-community/sqlite3.mbt). PRAGMA settings must be read back because unknown names are silently ignored. No power-loss durability claim beyond SQLite/OS/filesystem guarantees is inferred from a process-kill test.
