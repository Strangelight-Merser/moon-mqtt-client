# ADR: future connection refusal policy

Status: selected direction for v0.9 research; **no v0.8 runtime change**.

The current v0.8 candidate retains its existing initial, ordinary recovery and
maintenance policies, retry budgets and reset points. `set_reconnect_seed` stays
public for deterministic tests; its eventual deprecation needs separate review.

| Origin and input | v0.9 direction |
|---|---|
| Initial dial failure | Fail fast. |
| Ordinary MQTT 3.1.1 recovery CONNACK rc 3 | Bounded retry within the ordinary dial budget. |
| Ordinary MQTT 3.1.1 recovery CONNACK rc 1/2/4/5 | Terminal. |
| Ordinary MQTT 5 recovery CONNACK `0x88`, `0x89`, `0x9F` | Finite backoff within the ordinary dial budget; cover `0x9F` independently. |
| Other or unclassified negative CONNACK | Terminal until typed/numeric evidence justifies a narrower rule. |
| MQTT 5 `0x9C`/`0x9D` redirect | No automatic endpoint change. |
| Maintenance reconnect | Only the already specified server DISCONNECT reasons may trigger it; a direct negative CONNACK terminates that attempt. |
| Temporary TCP/TLS I/O after maintenance starts | May enter ordinary recovery; it does not replenish the maintenance lifetime budget. |
| Permanent certificate or identity error | Classify from typed transport evidence, never display text. |
| PUBACK refusal | Ends only that publication exchange; it is not a dial refusal. |

Name and account for three budgets independently: ordinary dial, maintenance
lifetime and delivery attachment. A future optional `RetryOptions` and stable
window are API design work. Thirty seconds is only a candidate stable-window
value; compare 0/5/30 seconds before changing any reset. Preserve packet ID,
DUP, Session Present, expiry, `NotSent` and `OutcomeUnknown` semantics.

The numeric CONNACK names follow the [MQTT 3.1.1 standard](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html)
and [MQTT 5.0 standard](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html).
This ADR selects client behavior for later implementation; it is not a claim
that the standards require this retry policy.
