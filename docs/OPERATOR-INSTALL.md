# Recovery operator bundle

The versioned operator ZIP is built from the same source snapshot as the library
candidate. It contains `durable_recovery.py`, `requirements.txt`, the recovery
contract, this guide, provenance and `SHA256SUMS.json`. It is separate from the
Mooncakes library. Python 3.10+ with standard-library SQLite and
`paho-mqtt==2.1.0` are required. SQLite must support schema-2 stores and the local
filesystem must support hard links and fsync. Windows is not accepted by the
current release matrix.

Extract to a new directory, verify each file against `SHA256SUMS.json` using a
trusted copy of the bundle checksum, then create an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python durable_recovery.py --help
.venv/bin/python durable_recovery.py inspect --source /absolute/path/old.sqlite3
.venv/bin/python durable_recovery.py export --source /absolute/path/old.sqlite3 --archive /absolute/path/evidence.mqttrec
.venv/bin/python durable_recovery.py status --source /absolute/path/old.sqlite3
.venv/bin/python durable_recovery.py resolve --help
.venv/bin/python durable_recovery.py resume --help
```

`resolve` and `resume` are experimental operator operations with broker and local
storage effects. Follow `DURABLE-RECOVERY.md` to prepare and approve a complete
request. Online operation is plain TCP only. Do not downgrade a TLS/WS identity
or upload archives, credentials or device-private configuration with bug reports.
Hashes are not encryption or origin authentication.

`tests/operator_consumer.py` performs clean installation and the complete recovery
administration suite against the unpacked CLI. Its native inspector and reopened
client are compiled against the unpacked library candidate. Synthetic test rows
and local peers do not stand in for a decision about a real user's records.
