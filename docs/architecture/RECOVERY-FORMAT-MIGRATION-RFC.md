# Recovery format support and schema 3 research

Status: format commitment and migration research, not a schema 3 implementation.
The library and operator ZIP have independent versions and must be named
separately in every candidate manifest. The current module version remains
`0.7.1`; an audit candidate ID does not create a registry version.

| Format | Current support |
|---|---|
| Durable SQLite schema `2` | Library opens and verifies exact schema/identity; operator inspects and exports. |
| Recovery request `1` | Operator validates exact fields before mutation. |
| Recovery archive `1` | Operator builds and verifies canonical bytes and source snapshot. |
| Sidecar journal `1` | Operator resumes only a matching operation and validated phase. |
| Unknown future version | Reject before mutation; no guessed migration. |

The v0.8 series continues reading and verifying the delivered v1 recovery
formats. `resolve` and `resume` remain experimental and online cleanup remains
plain TCP only. Each operator release should list the library versions and
format versions it was tested against, plus clean-install command results.

Schema 3 requires a separate migration RFC and native proof of:

1. Copy the entire schema 2 store to a new path and verify both copies before
   publishing a replacement. Keep the old store and its identity intact.
2. Define the logical session identity and broker cleanup transition. Never
   attach uncertain old records to a fresh session by inference.
3. Preserve each unconfirmed record, packet ID, DUP and disposition evidence;
   require an explicit operator decision where outcome is unknown.
4. Crash after each copy, flush, archive, identity and publish boundary, then
   resume or roll back without replacing the only valid old file.
5. Test old-library refusal, new-library open, repeated resume and rollback
   from a clean directory with exact package consumers.

Durable inbox, QoS 2, more MQTT 5 features and additional storage backends
remain separate research items. None is scheduled as a v0.8 implementation.
