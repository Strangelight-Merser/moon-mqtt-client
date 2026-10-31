# Security and data-conservation acceptance

The relevant trust boundaries are the broker/transport, untrusted MQTT packet
bytes and reason text, local credential files, the operator archive directory,
and the application's own message processing. TLS authenticates peers according
to the configured CA/hostname/client identity; it does not validate business
commands. A trusted broker can still send malformed traffic.

| Boundary | Acceptance evidence / invariant |
|---|---|
| Server identity | Untrusted CA and hostname mismatch fail in the real TLS suite |
| Client identity | Wrong/expired certificate, incomplete chain, mismatched/malformed/encrypted private key and missing files are independent negatives |
| Packet memory | Declared huge length fails before reading/allocating that body; packet/property limits checked by raw peers and codec tests |
| Legal small messages | Bounded receive/control queue overflow is tested; flooding does not create an unbounded queue |
| Partial frame and ACK | Raw fault peers with bounded watchdogs; no fabricated request success |
| Reason text | Numeric reason remains authoritative even with misleading text, CR/LF or ESC; treat displayed text as untrusted and escape it in structured logs/UI |
| Reconnect pressure | Separate bounded budgets and queue invariants; repeated recoveries measured on live native PIDs |
| Durable ownership | Admission COMMIT, possible-write COMMIT, ACK DELETE COMMIT retain their existing order; schema stays 2 |
| Operator export/resolve/resume | Archive conservation, source retirement, no-overwrite, journal Requested/Confirmed and Ready/Published windows; native old-store rejection and new-store open checks |
| Disk-full and I/O | Isolated small volume actually reaches ENOSPC; separate child-only fsync/pwrite EIO injection; no hidden success or archive overwrite |

Recovery archives contain business IDs, topics and payloads. Their hashes detect
byte changes; they are neither encryption nor source authentication. Permissions
and no-overwrite behavior do not make an archive safe to publish. Keep real
archives, private keys and device configuration out of shared artifacts.
The operator ZIP contains source/docs/dependency declarations, not user data.
`resolve`/`resume` remain experimental operator actions with conservation checks.
Online recovery is explicitly the supported plain-TCP case; never downgrade a
TLS-identified store to bypass identity validation.

A successful broker PUBACK is not a consumer transaction. Incoming PUBACK is not
coupled to an application database commit; there is no durable inbox. Business
side effects need their own reconciliation and independently observed outcomes.
The ESP32 example's RAM deduplication is bounded and is lost on reboot.

The native SQLite layer may classify a full-volume failure as
`DurableStoreIo("... unable to open database file")`; tests preserve the actual
cause instead of demanding a fictitious `DurableStoreFull` label. Improving that
public classification needs a separately reviewed contract change.

Syscall EIO injection, process-crash tests and real ENOSPC each establish their
stated fault model. None establishes real electrical power-loss resilience.
Release reports preserve first failures and evidence gaps; no security claim is
inferred solely from a test count or a passing happy path.
