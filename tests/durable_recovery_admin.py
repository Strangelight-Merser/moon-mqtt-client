#!/usr/bin/env python3
"""Black-box acceptance for whole-session durable recovery administration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from contextlib import closing


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(os.environ.get("PYTHON", ROOT / ".venv/bin/python"))
CLI = ROOT / "scripts/durable_recovery.py"


def identity(host: str, port: int, client_id: str, protocol: str = "mqtt311",
             expiry: int = 0, username: str | None = None) -> str:
    version = "311" if protocol == "mqtt311" else "5"
    user = "none" if username is None else f"some:{len(username.encode())}:{username}"
    return (
        f"moon-mqtt-durable-v2|{version}|resume|{expiry}|mqtt|"
        f"{len(host.encode())}:{host}|{port}||"
        f"{len(client_id.encode())}:{client_id}|{user}"
    )


def create_store(path: Path, stored_identity: str, *, blocked: bool = True) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        PRAGMA user_version = 2;
        CREATE TABLE durable_meta (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          schema_version INTEGER NOT NULL,
          identity TEXT NOT NULL,
          next_sequence INTEGER NOT NULL CHECK (next_sequence > 0),
          known_session INTEGER NOT NULL CHECK (known_session IN (0, 1))
        );
        CREATE TABLE durable_outbox (
          delivery_id TEXT PRIMARY KEY,
          packet_id INTEGER NOT NULL UNIQUE CHECK (packet_id BETWEEN 1 AND 65535),
          sequence INTEGER NOT NULL UNIQUE CHECK (sequence > 0),
          topic TEXT NOT NULL,
          payload BLOB NOT NULL,
          properties BLOB NOT NULL,
          message_expiry_at_ms INTEGER CHECK (message_expiry_at_ms IS NULL OR message_expiry_at_ms > 0),
          retain INTEGER NOT NULL CHECK (retain IN (0, 1)),
          expires_at_ms INTEGER NOT NULL,
          attempts INTEGER NOT NULL CHECK (attempts >= 0),
          ever_started INTEGER NOT NULL CHECK (ever_started IN (0, 1)),
          state INTEGER NOT NULL CHECK (state IN (0, 1)),
          reason TEXT NOT NULL
        );
    """)
    connection.execute(
        "INSERT INTO durable_meta VALUES (1, 2, ?, 2, 1)",
        (stored_identity,),
    )
    connection.execute(
        "INSERT INTO durable_outbox VALUES (?, ?, 1, ?, ?, ?, NULL, 0, ?, 2, 1, ?, ?)",
        (
            "old-delivery", 17, "devices/relay", b"\x00old-command\xff", b"\x00",
            9_999_999_999_999, 1 if blocked else 0,
            "broker returned Session Present=false" if blocked else "",
        ),
    )
    connection.commit()
    connection.close()


def packet(connection: socket.socket) -> bytes:
    head = connection.recv(1)
    if not head:
        return b""
    encoded = bytearray()
    size, multiplier = 0, 1
    while True:
        digit = connection.recv(1)
        if not digit:
            raise EOFError("truncated MQTT remaining length")
        encoded.extend(digit)
        size += (digit[0] & 127) * multiplier
        if not digit[0] & 128:
            break
        multiplier *= 128
    body = bytearray()
    while len(body) < size:
        chunk = connection.recv(size - len(body))
        if not chunk:
            raise EOFError("truncated MQTT packet")
        body.extend(chunk)
    return head + bytes(encoded) + bytes(body)


def packet_body(value: bytes) -> bytes:
    index, multiplier, size = 1, 1, 0
    while True:
        digit = value[index]
        index += 1
        size += (digit & 127) * multiplier
        if not digit & 128:
            return value[index:index + size]
        multiplier *= 128


def variable_integer(value: bytes, index: int) -> tuple[int, int]:
    size, multiplier = 0, 1
    while True:
        digit = value[index]
        index += 1
        size += (digit & 127) * multiplier
        if not digit & 128:
            return size, index
        multiplier *= 128


def connect_info(value: bytes) -> dict[str, object]:
    body = packet_body(value)
    protocol_length = int.from_bytes(body[:2], "big")
    index = 2
    protocol_name = body[index:index + protocol_length]
    index += protocol_length
    level, flags = body[index], body[index + 1]
    index += 4  # level, flags, keepalive
    properties = b""
    if level == 5:
        property_length, after_length = variable_integer(body, index)
        properties = body[after_length:after_length + property_length]
        index = after_length + property_length
    client_length = int.from_bytes(body[index:index + 2], "big")
    index += 2
    client_id = body[index:index + client_length].decode("utf-8")
    return {
        "protocol_name": protocol_name, "level": level, "flags": flags,
        "properties": properties, "client_id": client_id,
    }


class CleanPeer:
    def __init__(
        self, outcomes=(0, 0), *, mqtt5: bool = False,
        session_present: bool = False, connack_expiry: int | None = None,
        expect_publish_after: bool = False,
    ):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(8)
        self.port = self.listener.getsockname()[1]
        self.outcomes = outcomes
        self.mqtt5 = mqtt5
        self.session_present = session_present
        self.connack_expiry = connack_expiry
        self.expect_publish_after = expect_publish_after
        self.connects: list[bytes] = []
        self.trailing: list[bytes] = []
        self.application_connect = b""
        self.application_publish = b""
        self.errors: list[Exception] = []
        self.worker = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.worker.start()

    def _run(self) -> None:
        try:
            for result in self.outcomes:
                connection, _ = self.listener.accept()
                connection.settimeout(3)
                with connection:
                    connect = packet(connection)
                    self.connects.append(connect)
                    if self.mqtt5:
                        properties = b""
                        if self.connack_expiry is not None:
                            properties = b"\x11" + self.connack_expiry.to_bytes(4, "big")
                        body = (
                            bytes([1 if self.session_present else 0, result, len(properties)]) +
                            properties
                        )
                        connection.sendall(b"\x20" + bytes([len(body)]) + body)
                    else:
                        connection.sendall(
                            b"\x20\x02" + bytes([1 if self.session_present else 0, result])
                        )
                    if result == 0:
                        try:
                            tail = packet(connection)
                        except (socket.timeout, ConnectionResetError):
                            tail = b""
                        self.trailing.append(tail)
            if self.expect_publish_after:
                connection, _ = self.listener.accept()
                connection.settimeout(3)
                with connection:
                    self.application_connect = packet(connection)
                    connection.sendall(b"\x20\x02\x00\x00")
                    self.application_publish = packet(connection)
                    body = packet_body(self.application_publish)
                    topic_length = int.from_bytes(body[:2], "big")
                    packet_index = 2 + topic_length
                    packet_id = body[packet_index:packet_index + 2]
                    connection.sendall(b"\x40\x02" + packet_id)
                    self.trailing.append(packet(connection))
        except Exception as error:
            self.errors.append(error)
        finally:
            self.listener.close()

    def finish(self) -> None:
        self.worker.join(10)
        if self.worker.is_alive():
            raise AssertionError("cleanup peer did not finish")
        if self.errors:
            raise AssertionError(self.errors)


class DurableRecoveryAdmin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        moon = os.environ.get("MOON", str(ROOT / "scripts/moon.sh"))
        subprocess.run([moon, "build", "--target", "native"], cwd=ROOT, check=True)
        matches = list((ROOT / "_build/native/debug/build").glob(
            "**/tests/recovery_inspector/recovery_inspector.exe"
        ))
        if len(matches) != 1:
            raise AssertionError(f"expected one recovery inspector, found {matches}")
        cls.native_inspector = matches[0]
        durable_matches = list((ROOT / "_build/native/debug/build").glob(
            "**/examples/durable_recovery_driver/durable_recovery_driver.exe"
        ))
        if len(durable_matches) != 1:
            raise AssertionError(f"expected one durable recovery driver, found {durable_matches}")
        cls.durable_driver = durable_matches[0]

    def run_cli(self, *args: str, ok: bool = True, env=None) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [str(PYTHON), str(CLI), *args], cwd=ROOT,
            text=True, capture_output=True, timeout=20, env=env,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_native_inspector(
        self, outbox: Path, port: int, client_id: str,
        *, protocol: str = "mqtt311", expiry: int = 0, ok: bool = True,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update({
            "RECOVERY_OUTBOX": str(outbox),
            "RECOVERY_HOST": "127.0.0.1",
            "RECOVERY_PORT": str(port),
            "RECOVERY_CLIENT_ID": client_id,
            "RECOVERY_PROTOCOL": protocol,
            "RECOVERY_SESSION_EXPIRY": str(expiry),
        })
        result = subprocess.run(
            [str(self.native_inspector)], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=10,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def request(self, directory: Path, port: int) -> tuple[Path, dict]:
        source = directory / "old.sqlite3"
        target = directory / "new.sqlite3"
        archive = directory / "recovery.mqttrec"
        old_id, new_id = "recovery-old", "recovery-new"
        create_store(source, identity("127.0.0.1", port, old_id))
        request = {
            "version": 1,
            "operation_id": "op-001",
            "source": str(source),
            "archive": str(archive),
            "target": str(target),
            "broker": {
                "host": "127.0.0.1", "port": port,
                "protocol": "mqtt311", "transport": "tcp",
            },
            "old_session": {"client_id": old_id, "session_expiry_secs": 0},
            "new_session": {"client_id": new_id, "session_expiry_secs": 0},
            "decision": {
                "operator": "acceptance-test",
                "decided_at": "2026-09-21T00:00:00Z",
                "rationale": "archive uncertain command and start a fresh identity",
                "same_principal_asserted": True,
                "old_client_id_owned": True,
                "old_cleanup_authorized": True,
                "new_client_id_owned": True,
                "new_cleanup_authorized": True,
                "records": [{
                    "delivery_id": "old-delivery",
                    "disposition": "AbandonedUnknown",
                }],
            },
        }
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        return request_path, request

    def mqtt5_request(self, directory: Path, port: int) -> tuple[Path, dict]:
        request_path, request = self.request(directory, port)
        request["broker"]["protocol"] = "mqtt5"
        request["old_session"]["session_expiry_secs"] = 120
        request["new_session"]["session_expiry_secs"] = 120
        create_path = Path(request["source"])
        create_path.unlink()
        create_store(
            create_path,
            identity("127.0.0.1", port, "recovery-old", "mqtt5", 120),
        )
        request_path.write_text(json.dumps(request), encoding="utf-8")
        return request_path, request

    def test_inspect_missing_is_no_create_and_export_detects_tamper(self):
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-observe-") as temp:
            directory = Path(temp)
            missing = directory / "missing.sqlite3"
            self.run_cli("inspect", "--source", str(missing), ok=False)
            self.assertFalse(missing.exists())

            source = directory / "old.sqlite3"
            create_store(source, identity("127.0.0.1", 1883, "old"))
            before = source.read_bytes()
            inspected = self.run_cli("inspect", "--source", str(source))
            snapshot = json.loads(inspected.stdout)
            self.assertEqual(snapshot["schema_version"], 2)
            self.assertEqual(snapshot["records"][0]["payload_base64"], "AG9sZC1jb21tYW5k/w==")
            self.assertEqual(source.read_bytes(), before)

            archive = directory / "evidence.mqttrec"
            self.run_cli("export", "--source", str(source), "--archive", str(archive))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.read_bytes(), before)
            with archive.open("r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                handle.write(b"X")
            self.run_cli("verify", "--archive", str(archive), ok=False)

    def test_resolve_cleans_both_ids_retains_source_and_publishes_empty_target(self):
        peer = CleanPeer(expect_publish_after=True)
        peer.start()
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-resolve-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, peer.port)
            result = self.run_cli("resolve", "--request", str(request_path))
            self.assertEqual(json.loads(result.stdout)["phase"], "Complete")
            self.assertEqual(len(peer.connects), 2)
            self.assertTrue(all(value[0] == 0x10 for value in peer.connects))
            info = [connect_info(value) for value in peer.connects]
            self.assertEqual([value["client_id"] for value in info], [
                "recovery-old", "recovery-new",
            ])
            self.assertTrue(all(value["level"] == 4 for value in info))
            self.assertTrue(all(value["flags"] & 0x02 for value in info))
            self.assertTrue(all(not value["flags"] & 0x04 for value in info))
            self.assertTrue(all(value in (b"", b"\xe0\x00") for value in peer.trailing))
            source = Path(request["source"])
            target = Path(request["target"])
            with closing(sqlite3.connect(source)) as connection:
                meta = connection.execute(
                    "SELECT schema_version, identity FROM durable_meta"
                ).fetchone()
                rows = connection.execute("SELECT delivery_id FROM durable_outbox").fetchall()
            self.assertEqual(meta[0], 2)
            self.assertTrue(meta[1].startswith("moon-mqtt-retired-v1|op-001|"))
            self.assertEqual(rows, [("old-delivery",)])
            with closing(sqlite3.connect(target)) as connection:
                target_meta = connection.execute(
                    "SELECT schema_version, identity, known_session FROM durable_meta"
                ).fetchone()
                count = connection.execute("SELECT count(*) FROM durable_outbox").fetchone()[0]
            self.assertEqual(target_meta, (
                2, identity("127.0.0.1", peer.port, "recovery-new"), 0,
            ))
            self.assertEqual(count, 0)

            self.run_native_inspector(
                source, peer.port, "recovery-old", ok=False,
            )
            native_target = self.run_native_inspector(
                target, peer.port, "recovery-new",
            )
            self.assertIn("records=0", native_target.stdout)

            driver_env = os.environ.copy()
            driver_env.update({
                "D1_OUTBOX": str(target), "D1_PORT": str(peer.port),
                "D1_MODE": "run", "D1_CLIENT_ID": "recovery-new",
                "D1_DELIVERY_ID": "new-delivery", "D1_TOPIC": "devices/new",
                "D1_PAYLOAD": "new-command", "D1_RECONNECT_ATTEMPTS": "0",
                "D1_OPERATION_TIMEOUT_MS": "3000",
            })
            published = subprocess.run(
                [str(self.durable_driver)], cwd=ROOT, env=driver_env,
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
            self.assertIn("acknowledged id=new-delivery", published.stdout)
            peer.finish()
            self.assertEqual(connect_info(peer.application_connect)["client_id"], "recovery-new")
            self.assertFalse(connect_info(peer.application_connect)["flags"] & 0x02)
            publish_body = packet_body(peer.application_publish)
            topic_length = int.from_bytes(publish_body[:2], "big")
            payload_index = 2 + topic_length + 2
            self.assertEqual(publish_body[payload_index:], b"new-command")
            self.assertNotIn(b"old-command", peer.application_publish)

            archived = self.run_cli("verify", "--archive", request["archive"])
            archive_value = json.loads(archived.stdout)
            archived_rows = archive_value["manifest"]["snapshot"]["records"]
            self.assertEqual(archived_rows[0]["delivery_id"], "old-delivery")
            self.assertEqual(archived_rows[0]["payload_base64"], "AG9sZC1jb21tYW5k/w==")

            # Complete is observational: no broker is listening and no cleanup repeats.
            repeated = self.run_cli("resume", "--request", str(request_path))
            self.assertEqual(json.loads(repeated.stdout)["phase"], "Complete")
            status = self.run_cli("status", "--source", str(source))
            self.assertEqual(json.loads(status.stdout)["phase"], "Complete")
            with closing(sqlite3.connect(str(source) + ".recovery.sqlite3")) as connection:
                connection.execute(
                    "UPDATE recovery_journal SET phase='UntrustedComplete'"
                )
                connection.commit()
            corrupted = self.run_cli("status", "--source", str(source), ok=False)
            self.assertIn("unknown recovery phase", corrupted.stderr)

    def test_mqtt5_clean_start_has_zero_expiry_and_distinct_ids(self):
        peer = CleanPeer(mqtt5=True)
        peer.start()
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-v5-") as temp:
            directory = Path(temp)
            request_path, request = self.mqtt5_request(directory, peer.port)
            result = self.run_cli("resolve", "--request", str(request_path))
            self.assertEqual(json.loads(result.stdout)["phase"], "Complete")
            peer.finish()
            info = [connect_info(value) for value in peer.connects]
            self.assertEqual([value["client_id"] for value in info], [
                "recovery-old", "recovery-new",
            ])
            self.assertTrue(all(value["level"] == 5 for value in info))
            self.assertTrue(all(value["flags"] & 0x02 for value in info))
            self.assertTrue(all(not value["flags"] & 0x04 for value in info))
            self.assertTrue(all(value["properties"] == b"\x11\x00\x00\x00\x00" for value in info))
            with closing(sqlite3.connect(request["target"])) as connection:
                value = connection.execute("SELECT identity FROM durable_meta").fetchone()[0]
            self.assertEqual(
                value,
                identity("127.0.0.1", peer.port, "recovery-new", "mqtt5", 120),
            )
            self.run_native_inspector(
                Path(request["source"]), peer.port, "recovery-old",
                protocol="mqtt5", expiry=120, ok=False,
            )
            native_target = self.run_native_inspector(
                Path(request["target"]), peer.port, "recovery-new",
                protocol="mqtt5", expiry=120,
            )
            self.assertIn("records=0", native_target.stdout)

    def test_cleanup_rejects_session_present_and_nonzero_server_expiry(self):
        fixtures = (
            (CleanPeer(outcomes=(0,), session_present=True), False),
            (CleanPeer(outcomes=(0,), mqtt5=True, connack_expiry=30), True),
        )
        for peer, mqtt5 in fixtures:
            with self.subTest(mqtt5=mqtt5), tempfile.TemporaryDirectory(
                prefix="mqtt-recovery-clean-negative-",
            ) as temp:
                peer.start()
                directory = Path(temp)
                request_path, request = (
                    self.mqtt5_request(directory, peer.port)
                    if mqtt5 else self.request(directory, peer.port)
                )
                rejected = self.run_cli("resolve", "--request", str(request_path), ok=False)
                peer.finish()
                expected = "Session Expiry" if mqtt5 else "Session Present"
                self.assertIn(expected, rejected.stderr)
                self.assertFalse(Path(request["target"]).exists())

    def test_missing_authorization_same_id_alias_and_existing_target_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-reject-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, 9)
            request["decision"]["new_cleanup_authorized"] = False
            request_path.write_text(json.dumps(request), encoding="utf-8")
            self.run_cli("resolve", "--request", str(request_path), ok=False)
            self.assertFalse(Path(request["archive"]).exists())

            request["decision"]["new_cleanup_authorized"] = True
            request["new_session"]["client_id"] = request["old_session"]["client_id"]
            request_path.write_text(json.dumps(request), encoding="utf-8")
            self.run_cli("resolve", "--request", str(request_path), ok=False)

            request["new_session"]["client_id"] = "recovery-new"
            request["target"] = request["source"]
            request_path.write_text(json.dumps(request), encoding="utf-8")
            self.run_cli("resolve", "--request", str(request_path), ok=False)

            request["target"] = str(directory / "new.sqlite3")
            Path(request["target"]).write_text("owned", encoding="utf-8")
            source_before = Path(request["source"]).read_bytes()
            request_path.write_text(json.dumps(request), encoding="utf-8")
            self.run_cli("resolve", "--request", str(request_path), ok=False)
            self.assertEqual(Path(request["target"]).read_text(), "owned")
            self.assertEqual(Path(request["source"]).read_bytes(), source_before)
            self.assertFalse(Path(request["archive"]).exists())
            self.assertFalse(Path(request["source"] + ".recovery.sqlite3").exists())

            request["broker"]["password"] = "must-never-be-archived"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            rejected = self.run_cli("resolve", "--request", str(request_path), ok=False)
            self.assertIn("unsupported fields", rejected.stderr)

    def test_busy_observation_and_refused_connack_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-busy-") as temp:
            directory = Path(temp)
            source = directory / "busy.sqlite3"
            create_store(source, identity("127.0.0.1", 1883, "busy"))
            owner = sqlite3.connect(source, timeout=0, isolation_level=None)
            owner.execute("PRAGMA locking_mode=EXCLUSIVE")
            owner.execute("BEGIN EXCLUSIVE")
            owner.execute("COMMIT")
            try:
                locked = self.run_cli("inspect", "--source", str(source), ok=False)
                self.assertIn("locked", locked.stderr.lower())
            finally:
                owner.close()

        peer = CleanPeer(outcomes=(5,))
        peer.start()
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-refused-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, peer.port)
            refused = self.run_cli("resolve", "--request", str(request_path), ok=False)
            self.assertIn("CONNACK was not accepted", refused.stderr)
            peer.finish()
            self.assertFalse(Path(request["target"]).exists())
            journal = sqlite3.connect(request["source"] + ".recovery.sqlite3")
            try:
                phase = journal.execute("SELECT phase FROM recovery_journal").fetchone()[0]
            finally:
                journal.close()
            self.assertEqual(phase, "OldCleanRequested")

    def test_resume_reconciles_side_effect_before_phase_commit_windows(self):
        for failpoint in (
            "after_archive_publish", "after_journal_publish", "after_source_retire",
            "after_old_clean", "after_new_clean", "after_replacement_ready",
            "after_target_publish",
        ):
            with self.subTest(failpoint=failpoint), tempfile.TemporaryDirectory(
                prefix=f"mqtt-recovery-{failpoint}-",
            ) as temp:
                broker_uncertain = failpoint in ("after_old_clean", "after_new_clean")
                peer = CleanPeer(outcomes=(0, 0, 0) if broker_uncertain else (0, 0))
                peer.start()
                directory = Path(temp)
                request_path, request = self.request(directory, peer.port)
                env = os.environ.copy()
                env["MOON_MQTT_RECOVERY_FAILPOINT"] = failpoint
                crashed = self.run_cli(
                    "resolve", "--request", str(request_path), ok=False, env=env,
                )
                self.assertEqual(crashed.returncode, 91, crashed.stdout + crashed.stderr)

                result = self.run_cli("resume", "--request", str(request_path))
                self.assertEqual(json.loads(result.stdout)["phase"], "Complete")
                peer.finish()
                self.assertEqual(len(peer.connects), 3 if broker_uncertain else 2)
                with closing(sqlite3.connect(request["source"])) as connection:
                    identity_value = connection.execute(
                        "SELECT identity FROM durable_meta"
                    ).fetchone()[0]
                    rows = connection.execute(
                        "SELECT delivery_id, packet_id, payload FROM durable_outbox"
                    ).fetchall()
                self.assertTrue(identity_value.startswith("moon-mqtt-retired-v1|op-001|"))
                self.assertEqual(rows, [("old-delivery", 17, b"\x00old-command\xff")])
                with closing(sqlite3.connect(request["target"])) as connection:
                    self.assertEqual(
                        connection.execute("SELECT count(*) FROM durable_outbox").fetchone()[0],
                        0,
                    )

    def test_source_inode_replacement_and_trigger_are_rejected_before_retirement(self):
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-inode-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, 9)
            env = os.environ.copy()
            env["MOON_MQTT_RECOVERY_FAILPOINT"] = "after_journal_publish"
            crashed = self.run_cli(
                "resolve", "--request", str(request_path), ok=False, env=env,
            )
            self.assertEqual(crashed.returncode, 91)
            source = Path(request["source"])
            moved = directory / "moved-original.sqlite3"
            source.rename(moved)
            shutil.copy2(moved, source)
            rejected = self.run_cli("resume", "--request", str(request_path), ok=False)
            self.assertIn("source file identity", rejected.stderr)
            for path in (source, moved):
                with closing(sqlite3.connect(path)) as connection:
                    value = connection.execute("SELECT identity FROM durable_meta").fetchone()[0]
                self.assertEqual(value, identity("127.0.0.1", 9, "recovery-old"))

        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-trigger-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, 9)
            with closing(sqlite3.connect(request["source"])) as connection:
                connection.execute(
                    "CREATE TRIGGER hostile AFTER UPDATE OF identity ON durable_meta "
                    "BEGIN DELETE FROM durable_outbox; END"
                )
            rejected = self.run_cli("resolve", "--request", str(request_path), ok=False)
            self.assertIn("unexpected durable schema object", rejected.stderr)
            with closing(sqlite3.connect(request["source"])) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM durable_outbox").fetchone()[0], 1,
                )
            self.assertFalse(Path(request["archive"]).exists())

    def test_recorded_replacement_symlink_is_rejected_without_following_it(self):
        peer = CleanPeer()
        peer.start()
        with tempfile.TemporaryDirectory(prefix="mqtt-recovery-stage-link-") as temp:
            directory = Path(temp)
            request_path, request = self.request(directory, peer.port)
            env = os.environ.copy()
            env["MOON_MQTT_RECOVERY_FAILPOINT"] = "after_replacement_ready"
            crashed = self.run_cli(
                "resolve", "--request", str(request_path), ok=False, env=env,
            )
            self.assertEqual(crashed.returncode, 91)
            peer.finish()
            journal_path = Path(request["source"] + ".recovery.sqlite3")
            with closing(sqlite3.connect(journal_path)) as connection:
                stage = Path(connection.execute(
                    "SELECT staged_target FROM recovery_journal"
                ).fetchone()[0])
            stage.unlink()
            external = directory / "external-owned"
            external.write_bytes(b"do-not-touch")
            stage.symlink_to(external)
            rejected = self.run_cli("resume", "--request", str(request_path), ok=False)
            self.assertIn("not a link/device", rejected.stderr)
            self.assertEqual(external.read_bytes(), b"do-not-touch")
            self.assertFalse(Path(request["target"]).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
