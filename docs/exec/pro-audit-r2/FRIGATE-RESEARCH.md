# Independent consumer research: Frigate event mirror

Status: next-stage study and test brief. No new application or third-party
deployment is included in this candidate.

Frigate's documented MQTT prefix defaults to `frigate` and is configurable.
`<prefix>/events` emits `new`, `update` and `end` messages for tracked objects;
later updates can reuse the same event ID when snapshots or zones change. The
mirror must retain the latest state per ID and process every update, including
the final `end_time`. A permanent seen-ID dedupe set would lose updates.
Availability is a separate `<prefix>/available` topic and may be republished
after broker reconnection. `<prefix>/reviews` is another change feed, with its
own review IDs and updates. [Frigate MQTT reference](https://docs.frigate.video/integrations/mqtt/).

The [manual event API](https://docs.frigate.video/integrations/api/create-event-events-camera-name-label-create-post)
explicitly says manual creation does not publish an `/events` MQTT update. It
cannot serve as a wire oracle. Obtain test data from documented examples and a
controlled Frigate instance that actually emits MQTT tracked-object updates;
save raw topic/payload/time records with version and configuration. A synthetic
fixture tests parser and ordering only.

Next-stage task:

1. Create an independent application directory or repository that installs the
   reviewed candidate package from its documented archive. Copy no test driver,
   import no `internal` package and leave this library repository unchanged.
2. Subscribe read-only to configured availability and events topics. Record
   `new → update → end` for one ID, repeated updates, reconnect/re-subscribe,
   cancellation, backpressure and malformed payloads. Never publish Frigate
   control topics from the observer.
3. Evaluate clean installation, public API comprehension, typed error messages,
   bounded memory, FD/RSS after scope close and actual reconnect behavior.
   Compare mirrored state to captured wire updates, not only Frigate's API.
4. Report the package hash, consumer commit, Frigate version, broker version,
   raw records, unsupported cases and setup effort. A repository-owned study
   remains a study until an unrelated user sustains real use.

No third party was contacted. No claim of external adoption follows from the
existing extracted-package consumers.
