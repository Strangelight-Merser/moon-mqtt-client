# Failure semantics by phase

This describes current behavior. Refusal classification, budget reset rules and
new public diagnostics require the next Pro decision. A broker acknowledgement
is not downstream processing or physical execution.

## Connection policy

| Dial source / failure | Current action | Evidence |
|---|---|---|
| Initial TCP/connect failure | Return cause; callback never entered | runtime `!ever_ready` path |
| Initial MQTT311 negative CONNACK | `ConnectionRefused`; no retry | dial-policy peer, wire codes 1/3/5 |
| Initial MQTT5 negative CONNACK | Numeric `BrokerRejected`; no retry | wire codes 0x87/0x89/0x9C |
| Initial TLS verification failure | `TlsFailure`; no retry | untrusted-CA peer |
| Transport recovery negative CONNACK | Continue ordinary bounded recovery | MQTT311 and MQTT5 table rows |
| Transport recovery TLS failure | Continue ordinary recovery | successful TLS → bad CA → successful TLS |
| Maintenance dial negative MQTT5 CONNACK | Typed terminal `BrokerRejected` | 0x87/0x89/0x9C rows |
| Maintenance dial TLS failure | Consume this origin; continue ordinary recovery | successful TLS → 0x89 → bad CA → successful TLS |
| MQTT5 server DISCONNECT | Terminal by default | reason-reconnect suite |
| Opt-in DISCONNECT 0x89 / 0x8B | Maintenance lifetime budget | reason-reconnect suite |
| Other DISCONNECT / exhausted maintenance budget | Terminal | disallowed-code and malicious reason-text cases |
| Protocol error / control exhaustion | Terminal | raw peer and typed terminal-cause tests |
| Storage failure | Fail stop with `DurableStorage` | durable transition and storage-fault suites |

The ordinary attempt counter and delay reset when a connection becomes ready.
The maintenance counter and delay do **not** reset on successful CONNACK. A
maintenance origin applies to exactly one dial, including a failed TLS dial;
later network retries have the ordinary origin. MQTT311 has no maintenance
DISCONNECT reason path. Certificate/key loading `InvalidConfig` is distinct
from `TlsFailure`; its classification after a previously ready connection also
remains an unresolved policy decision. No new classification is introduced here.

MQTT311 numeric CONNACK codes are recorded by the peer from the wire table.
`ConnectionRefused(String)` is opaque: no policy or assertion parses its display
string. MQTT5 exposes numeric `BrokerReason`. A negative PUBACK rejects a
**publication** and must never be treated as a negative CONNACK.

## Requests, handles and durable records

| Phase / event | Ordinary request | Recoverable handle | Durable record |
|---|---|---|---|
| Invalid input / full capacity before admission | Rejected before send | No handle | No row |
| Accepted; generation lost before socket write | `NotSent` | Await supported session recovery | Pending row retained |
| Socket write begun; transport or ACK lost | `OutcomeUnknown` | Same packet ID; DUP after possible write | Possible-write COMMIT precedes socket call; row retained |
| QoS0 write completes | Local transport-write success | No QoS0 delivery handle | Explicit QoS1 only |
| Matching successful PUBACK | Success | Acknowledged | DELETE COMMIT precedes acknowledged completion |
| Negative MQTT5 PUBACK | Definite rejection | Rejected with reason | DELETE COMMIT precedes rejected completion |
| Waiter cancelled | May abort generation to avoid untracked work | Cancelling wait does not cancel delivery | Committed admission remains owned; storage transition is cancellation-protected |
| Scope closes | Settled using started bit | Terminal not-sent or unknown | Uncompleted rows retained for explicit reopen |
| Session lost / terminal protocol / exhausted budget | Cannot continue old request | Terminal cause plus not-sent/unknown | Active rows blocked before releasing ownership |
| Initial connect fails before readiness | No application callback | Recovered handles settle locally | Existing pending rows preserved for later explicit reopen |
| Expiry before any possible write | Locally rejected | Terminal not-sent | Safe removal; immutable deadline |
| Expiry after possible write | Outcome cannot be inferred | Terminal unknown | Quarantined/blocked evidence retained |
| Storage transition fails | Storage error surfaced | No fabricated ACK success | Inspect after failure; no automatic rebuild/migration |
| Incoming QoS1 PUBLISH | Protocol PUBACK | No durable incoming handle | No inbox/business-transaction coupling |

Absence of a row is not proof of not-sent: successful ACK deletion also removes
rows. A device can apply a command before the sender sees PUBACK. Use business
IDs, receiver deduplication and independent feedback for side effects. Existing
stats show pressure and generations, not stronger delivery guarantees. See
[delivery choices](DELIVERY-GUIDE.md).

## Evidence boundaries

`tests/integration/run.py` covers untrusted CA, hostname mismatch, wrong/expired
client certificate, incomplete chain, key mismatch, malformed/encrypted key,
missing files and TLS1.3. Raw peers retain malformed length, partial frame,
small-message overflow and wrong-ACK assertions. MQTT5 codec/runtime suites cover
property bounds; reason-text content cannot opt an unapproved code into retry.

Syscall injection models an I/O error in an isolated child. Process kill models
a process crash. Neither establishes real power-loss behavior. Physical B2
evidence is separate. Actual run results belong in the
[implementation ledger](exec/pro-audit/README.md); defining a test is not a pass.
