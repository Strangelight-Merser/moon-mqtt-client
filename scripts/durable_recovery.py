#!/usr/bin/env python3
"""Operator tooling for durable sessions; resolve/resume are experimental."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from typing import Any
from urllib.parse import quote
import zipfile

import paho.mqtt.client as paho
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties


DURABLE_SCHEMA_VERSION = 2
JOURNAL_SCHEMA_VERSION = 1
ARCHIVE_FORMAT_VERSION = 1
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_SOURCE_BYTES + MAX_MANIFEST_BYTES + 1024 * 1024
MAX_RECORDS = 65_535
BACKUP_TIMEOUT_SECONDS = 5.0
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ALLOWED_DISPOSITIONS = {
    "ConfirmedApplied", "ConfirmedNotApplied", "AbandonedUnknown",
}
PHASES = (
    "Prepared", "SourceRetired", "OldCleanRequested", "OldCleanConfirmed",
    "NewCleanRequested", "NewCleanConfirmed", "ReplacementReady",
    "ReplacementPublished", "Complete",
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUEST_EXAMPLE = r'''
Plain-TCP resolution request (edit every placeholder; this is not authorization):
{
  "version": 1,
  "operation_id": "replace-with-unique-operation-id",
  "source": "/absolute/path/old.sqlite3",
  "archive": "/absolute/path/recovery.mqttrec",
  "target": "/absolute/path/new.sqlite3",
  "broker": {
    "host": "127.0.0.1", "port": 1883, "protocol": "mqtt311",
    "transport": "tcp", "tls": "plain", "timeout_seconds": 5
  },
  "old_session": {"client_id": "old-owned-id", "session_expiry_secs": 0},
  "new_session": {"client_id": "new-owned-id", "session_expiry_secs": 0},
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
      {"delivery_id": "replace-with-inspected-id", "disposition": "AbandonedUnknown"}
    ]
  }
}
Resolution supports plain TCP only. inspect/export remain offline and do not
interpret the stored transport identity. Never treat this example as an
operator decision: inspect first and edit every identity, path, row, and claim.
'''


class RecoveryError(Exception):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_file_no_replace(staging: Path, destination: Path) -> None:
    try:
        os.link(staging, destination)
    except FileExistsError as error:
        raise RecoveryError(f"refusing to replace existing path: {destination}") from error
    except OSError as error:
        raise RecoveryError(
            f"cannot publish {destination} with create-new hard link: {error}"
        ) from error
    fsync_directory(destination.parent)
    staging.unlink()
    fsync_directory(destination.parent)


def maybe_fail(name: str) -> None:
    if os.environ.get("MOON_MQTT_RECOVERY_FAILPOINT") == name:
        os._exit(91)


def existing_path(raw: str, label: str) -> Path:
    supplied = Path(raw).expanduser()
    try:
        supplied_metadata = os.lstat(supplied)
        if not stat.S_ISREG(supplied_metadata.st_mode):
            raise RecoveryError(f"{label} must be a regular file, not a link/device: {supplied}")
        resolved = supplied.resolve(strict=True)
    except FileNotFoundError as error:
        raise RecoveryError(f"{label} does not exist: {supplied}") from error
    if not resolved.is_file():
        raise RecoveryError(f"{label} is not a regular file: {resolved}")
    return resolved


def prospective_path(raw: str, label: str) -> Path:
    supplied = Path(raw).expanduser()
    try:
        parent = supplied.parent.resolve(strict=True)
    except FileNotFoundError as error:
        raise RecoveryError(f"{label} parent does not exist: {supplied.parent}") from error
    if not parent.is_dir():
        raise RecoveryError(f"{label} parent is not a directory: {parent}")
    return parent / supplied.name


def sqlite_uri(path: Path, mode: str) -> str:
    return f"file:{quote(str(path), safe='/')}?mode={mode}"


def connect_sqlite(path: Path, mode: str) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            sqlite_uri(path, mode), uri=True, timeout=0, isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout = 0")
        return connection
    except sqlite3.Error as error:
        raise RecoveryError(f"cannot open SQLite {path} in mode={mode}: {error}") from error


EXPECTED_META_COLUMNS = [
    "singleton", "schema_version", "identity", "next_sequence", "known_session",
]
EXPECTED_OUTBOX_COLUMNS = [
    "delivery_id", "packet_id", "sequence", "topic", "payload", "properties",
    "message_expiry_at_ms", "retain", "expires_at_ms", "attempts",
    "ever_started", "state", "reason",
]
EXPECTED_JOURNAL_COLUMNS = [
    "singleton", "schema_version", "operation_id", "request_json",
    "request_sha256", "archive_path", "archive_sha256",
    "source_snapshot_sha256", "source_device", "source_inode", "old_identity",
    "target_identity", "retired_identity", "phase", "staged_target",
    "staged_target_sha256", "created_at", "updated_at",
]


def require_schema(connection: sqlite3.Connection) -> None:
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise RecoveryError(f"SQLite integrity_check failed: {integrity!r}")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != DURABLE_SCHEMA_VERSION:
            raise RecoveryError(f"unsupported durable schema version: {version}")
        schema_objects = connection.execute(
            "SELECT type, name, tbl_name FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = {
            name for object_type, name, _table in schema_objects
            if object_type == "table" and not name.startswith("sqlite_")
        }
        if tables != {"durable_meta", "durable_outbox"}:
            raise RecoveryError(f"unexpected durable tables: {sorted(tables)!r}")
        for object_type, name, table_name in schema_objects:
            allowed = (
                (object_type == "table" and name in tables) or
                (
                    object_type == "index" and name.startswith("sqlite_autoindex_") and
                    table_name in tables
                )
            )
            if not allowed:
                raise RecoveryError(
                    f"unexpected durable schema object: {object_type} {name}"
                )
        meta_columns = [row[1] for row in connection.execute("PRAGMA table_info(durable_meta)")]
        outbox_columns = [
            row[1] for row in connection.execute("PRAGMA table_info(durable_outbox)")
        ]
        if meta_columns != EXPECTED_META_COLUMNS or outbox_columns != EXPECTED_OUTBOX_COLUMNS:
            raise RecoveryError("durable schema-2 columns do not match the approved layout")
    except sqlite3.Error as error:
        raise RecoveryError(f"cannot validate durable schema: {error}") from error


def snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    require_schema(connection)
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    if page_size * page_count > MAX_SOURCE_BYTES:
        raise RecoveryError("durable source exceeds the recovery size limit")
    metadata = connection.execute(
        "SELECT schema_version, identity, next_sequence, known_session "
        "FROM durable_meta WHERE singleton=1"
    ).fetchall()
    if len(metadata) != 1:
        raise RecoveryError("durable metadata row is missing or duplicated")
    version, identity, next_sequence, known_session = metadata[0]
    if version != 2 or not isinstance(identity, str) or not identity:
        raise RecoveryError("durable metadata is invalid")
    if not isinstance(next_sequence, int) or next_sequence <= 0 or known_session not in (0, 1):
        raise RecoveryError("durable sequence/session metadata is invalid")
    rows = connection.execute(
        "SELECT delivery_id, packet_id, sequence, topic, payload, properties, "
        "message_expiry_at_ms, retain, expires_at_ms, attempts, ever_started, "
        "state, reason FROM durable_outbox ORDER BY sequence"
    ).fetchall()
    if len(rows) > MAX_RECORDS:
        raise RecoveryError("durable record count exceeds recovery limit")
    records = []
    total_blob_bytes = 0
    for row in rows:
        payload, properties = row[4], row[5]
        if not isinstance(payload, bytes) or not isinstance(properties, bytes):
            raise RecoveryError("durable payload/properties must be SQLite BLOB values")
        total_blob_bytes += len(payload) + len(properties)
        if total_blob_bytes > MAX_SOURCE_BYTES:
            raise RecoveryError("durable payload evidence exceeds recovery limit")
        if row[11] not in (0, 1) or row[7] not in (0, 1) or row[10] not in (0, 1):
            raise RecoveryError("durable row contains an invalid boolean/state value")
        records.append({
            "delivery_id": row[0], "packet_id": row[1], "sequence": row[2],
            "topic": row[3],
            "payload_base64": base64.b64encode(payload).decode("ascii"),
            "properties_base64": base64.b64encode(properties).decode("ascii"),
            "message_expiry_at_ms": row[6], "retain": bool(row[7]),
            "expires_at_ms": row[8], "attempts": row[9],
            "ever_started": bool(row[10]),
            "state": "Blocked" if row[11] == 1 else "Pending",
            "cause": row[12],
        })
    result = {
        "schema_version": version,
        "identity": identity,
        "next_sequence": next_sequence,
        "known_session": bool(known_session),
        "records": records,
        "evidence_limit": (
            "Rows deleted after acknowledgement are not enumerable; absence is not NotSent."
        ),
    }
    result["snapshot_sha256"] = sha256_bytes(canonical_json(result))
    return result


def observation_snapshot(path: Path) -> dict[str, Any]:
    connection = connect_sqlite(path, "ro")
    try:
        connection.execute("BEGIN")
        result = snapshot(connection)
        connection.execute("COMMIT")
        return result
    except sqlite3.Error as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise RecoveryError(f"cannot inspect durable source: {error}") from error
    finally:
        connection.close()


def acquire_resolution_source(
    path: Path,
) -> tuple[sqlite3.Connection, dict[str, Any], dict[str, int]]:
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise RecoveryError("durable source is not a regular file")
    connection = connect_sqlite(path, "rw")
    try:
        require_schema(connection)
        mode = connection.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone()[0]
        if str(mode).lower() != "exclusive":
            raise RecoveryError("SQLite did not accept exclusive locking mode")
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("COMMIT")
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() != "delete":
            raise RecoveryError("durable source journal_mode must be DELETE")
        connection.execute("PRAGMA synchronous=EXTRA")
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 3:
            raise RecoveryError("durable source did not accept synchronous=EXTRA")
        after = os.stat(path, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RecoveryError("durable source path changed while acquiring ownership")
        return connection, snapshot(connection), {
            "device": after.st_dev, "inode": after.st_ino,
        }
    except sqlite3.Error as error:
        connection.close()
        raise RecoveryError(f"cannot acquire exclusive source ownership: {error}") from error
    except Exception:
        connection.close()
        raise


def backup_database(source: sqlite3.Connection, destination: Path) -> None:
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS
    destination_connection = sqlite3.connect(destination, isolation_level=None)
    try:
        def progress(status: int, _remaining: int, _total: int) -> None:
            if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise RecoveryError("SQLite backup encountered a busy/locked source")
            if time.monotonic() > deadline:
                raise RecoveryError("SQLite backup exceeded its bounded deadline")

        source.backup(destination_connection, pages=64, progress=progress, sleep=0.0)
        require_schema(destination_connection)
    except sqlite3.Error as error:
        raise RecoveryError(f"cannot create consistent SQLite backup: {error}") from error
    finally:
        destination_connection.close()
    os.chmod(destination, 0o600)
    fsync_file(destination)


def archive_manifest(
    source: sqlite3.Connection,
    snapshot_value: dict[str, Any],
    kind: str,
    request: dict[str, Any] | None,
    source_file: dict[str, int],
    working_dir: Path,
) -> tuple[dict[str, Any], Path]:
    fd, name = tempfile.mkstemp(prefix=".recovery-source-", suffix=".sqlite3", dir=working_dir)
    os.close(fd)
    database = Path(name)
    try:
        backup_database(source, database)
        manifest: dict[str, Any] = {
            "archive_format_version": ARCHIVE_FORMAT_VERSION,
            "kind": kind,
            "snapshot": snapshot_value,
            "source_file": source_file,
            "source_database_sha256": sha256_file(database),
            "evidence_limit": snapshot_value["evidence_limit"],
        }
        if request is not None:
            manifest["request"] = request
            manifest["request_sha256"] = sha256_bytes(canonical_json(request))
        return manifest, database
    except Exception:
        database.unlink(missing_ok=True)
        raise


def build_archive(
    source: sqlite3.Connection,
    snapshot_value: dict[str, Any],
    archive: Path,
    kind: str,
    request: dict[str, Any] | None = None,
    source_file: dict[str, int] | None = None,
) -> str:
    if source_file is None:
        raise RecoveryError("source file identity is required for an archive")
    manifest, database = archive_manifest(
        source, snapshot_value, kind, request, source_file, archive.parent,
    )
    manifest_bytes = canonical_json(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        database.unlink(missing_ok=True)
        raise RecoveryError("archive manifest exceeds recovery limit")
    fd, name = tempfile.mkstemp(prefix=f".{archive.name}.", suffix=".tmp", dir=archive.parent)
    os.close(fd)
    staging = Path(name)
    os.chmod(staging, 0o600)
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_STORED) as bundle:
            database_info = zipfile.ZipInfo("source.sqlite3", ZIP_TIMESTAMP)
            database_info.compress_type = zipfile.ZIP_STORED
            database_info.create_system = 3
            database_info.external_attr = 0o600 << 16
            with database.open("rb") as source_handle, bundle.open(database_info, "w") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            info = zipfile.ZipInfo("manifest.json", ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            bundle.writestr(info, manifest_bytes)
        if staging.stat().st_size > MAX_ARCHIVE_BYTES:
            raise RecoveryError("archive exceeds recovery size limit")
        fsync_file(staging)
        publish_file_no_replace(staging, archive)
        os.chmod(archive, 0o600)
        return sha256_file(archive)
    finally:
        database.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)


def verify_archive(
    archive: Path,
    expected_kind: str | None = None,
    expected_request: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RecoveryError("archive exceeds recovery size limit")
    try:
        with archive.open("rb") as raw_handle:
            raw_handle.seek(-22, os.SEEK_END)
            end_record = raw_handle.read(22)
        if len(end_record) != 22 or end_record[:4] != b"PK\x05\x06" or end_record[-2:] != b"\x00\x00":
            raise RecoveryError("archive has a noncanonical or trailing ZIP end record")
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            if [entry.filename for entry in infos] != ["source.sqlite3", "manifest.json"]:
                raise RecoveryError("archive member list/order is not canonical")
            for entry in infos:
                if (
                    entry.date_time != ZIP_TIMESTAMP or
                    entry.compress_type != zipfile.ZIP_STORED or
                    entry.create_system != 3 or
                    entry.external_attr != 0o600 << 16 or
                    entry.extra != b"" or entry.comment != b"" or
                    entry.compress_size != entry.file_size
                ):
                    raise RecoveryError("archive member metadata is not canonical")
            if infos[0].file_size > MAX_SOURCE_BYTES or infos[1].file_size > MAX_MANIFEST_BYTES:
                raise RecoveryError("archive member exceeds recovery size limit")
            manifest_raw = bundle.read("manifest.json")
            if len(manifest_raw) != infos[1].file_size:
                raise RecoveryError("archive manifest is truncated")
            manifest = json.loads(manifest_raw)
            if canonical_json(manifest) != manifest_raw:
                raise RecoveryError("archive manifest is not canonical JSON")
            if manifest.get("archive_format_version") != ARCHIVE_FORMAT_VERSION:
                raise RecoveryError("unsupported archive format")
            if expected_kind is not None and manifest.get("kind") != expected_kind:
                raise RecoveryError("archive kind does not match the requested operation")
            if expected_request is not None:
                if manifest.get("request") != expected_request:
                    raise RecoveryError("archive request does not match")
                expected_hash = sha256_bytes(canonical_json(expected_request))
                if manifest.get("request_sha256") != expected_hash:
                    raise RecoveryError("archive request hash does not match")
            database_bytes = bundle.read("source.sqlite3")
            if len(database_bytes) != infos[0].file_size:
                raise RecoveryError("archived database is truncated")
            if sha256_bytes(database_bytes) != manifest.get("source_database_sha256"):
                raise RecoveryError("archived database hash does not match")
            descriptor, temporary_name = tempfile.mkstemp(prefix="moon-mqtt-archive-", suffix=".sqlite3")
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=True) as handle:
                    descriptor = -1
                    handle.write(database_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                archived_connection = connect_sqlite(temporary, "ro")
                try:
                    archived_snapshot = snapshot(archived_connection)
                finally:
                    archived_connection.close()
                if archived_snapshot != manifest.get("snapshot"):
                    raise RecoveryError("archive manifest does not match its SQLite evidence")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"invalid recovery archive: {error}") from error
    return manifest, sha256_file(archive)


def validate_request(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise RecoveryError("request version must be 1")
    allowed_top = {
        "version", "operation_id", "source", "archive", "target", "broker",
        "old_session", "new_session", "decision",
    }
    if set(raw) != allowed_top:
        raise RecoveryError(f"request fields must be exactly {sorted(allowed_top)!r}")
    operation_id = raw.get("operation_id")
    if not isinstance(operation_id, str) or not IDENTIFIER.fullmatch(operation_id):
        raise RecoveryError("operation_id has an invalid format")
    source = existing_path(str(raw.get("source", "")), "source")
    archive = prospective_path(str(raw.get("archive", "")), "archive")
    target = prospective_path(str(raw.get("target", "")), "target")
    journal = prospective_path(str(source) + ".recovery.sqlite3", "journal")
    paths = [source, archive, target, journal]
    if len({str(path) for path in paths}) != len(paths):
        raise RecoveryError("source/archive/target/journal paths must be distinct")
    existing = [path for path in paths if path.exists()]
    inodes = [(path.stat().st_dev, path.stat().st_ino) for path in existing]
    if len(inodes) != len(set(inodes)):
        raise RecoveryError("source/archive/target/journal paths must not alias")

    broker = raw.get("broker")
    if not isinstance(broker, dict):
        raise RecoveryError("broker configuration is required")
    allowed_broker = {
        "host", "port", "protocol", "transport", "tls", "username",
        "password_env", "timeout_seconds",
    }
    if not set(broker).issubset(allowed_broker):
        raise RecoveryError("broker contains unsupported fields; plaintext credentials are forbidden")
    host, port = broker.get("host"), broker.get("port")
    if (
        not isinstance(host, str) or not host or "\x00" in host or
        len(host.encode("utf-8")) > 65_535 or isinstance(port, bool) or
        not isinstance(port, int) or not 0 < port < 65536
    ):
        raise RecoveryError("broker host/port is invalid")
    if broker.get("protocol") not in ("mqtt311", "mqtt5"):
        raise RecoveryError("broker protocol must be mqtt311 or mqtt5")
    if broker.get("transport", "tcp") != "tcp" or broker.get("tls", "plain") != "plain":
        raise RecoveryError("B1 CLI supports plain TCP only; TLS/WebSocket is rejected")
    username = broker.get("username")
    password_env = broker.get("password_env")
    if username is not None and (
        not isinstance(username, str) or "\x00" in username or
        len(username.encode("utf-8")) > 65_535
    ):
        raise RecoveryError("broker username must be a string")
    if password_env is not None and (
        not isinstance(password_env, str) or not IDENTIFIER.fullmatch(password_env)
    ):
        raise RecoveryError("password_env has an invalid format")
    if password_env is not None and username is None:
        raise RecoveryError("password_env requires username")
    timeout = broker.get("timeout_seconds", 5)
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
        not math.isfinite(timeout) or not 0 < timeout <= 60
    ):
        raise RecoveryError("broker.timeout_seconds must be in (0, 60]")
    if password_env is not None and password_env not in os.environ:
        raise RecoveryError(f"credential environment variable is missing: {password_env}")

    sessions = []
    for label in ("old_session", "new_session"):
        session = raw.get(label)
        if not isinstance(session, dict):
            raise RecoveryError(f"{label} is required")
        if set(session) != {"client_id", "session_expiry_secs"}:
            raise RecoveryError(f"{label} fields must be client_id and session_expiry_secs")
        client_id = session.get("client_id")
        expiry = session.get("session_expiry_secs", 0)
        if (
            not isinstance(client_id, str) or not client_id or "\x00" in client_id or
            len(client_id.encode("utf-8")) > 65_535
        ):
            raise RecoveryError(f"{label}.client_id is invalid")
        if (
            isinstance(expiry, bool) or not isinstance(expiry, int) or
            not 0 <= expiry <= 4_294_967_295
        ):
            raise RecoveryError(f"{label}.session_expiry_secs is invalid")
        if broker["protocol"] == "mqtt311" and expiry != 0:
            raise RecoveryError("MQTT 3.1.1 durable identity requires expiry 0")
        if broker["protocol"] == "mqtt5" and expiry == 0:
            raise RecoveryError("MQTT 5 ResumeSession durable identity requires nonzero expiry")
        sessions.append((client_id, expiry))
    if sessions[0][0] == sessions[1][0]:
        raise RecoveryError("old and new client IDs must be different")

    decision = raw.get("decision")
    if not isinstance(decision, dict):
        raise RecoveryError("operator decision is required")
    allowed_decision = {
        "operator", "decided_at", "rationale", "same_principal_asserted",
        "old_client_id_owned", "old_cleanup_authorized", "new_client_id_owned",
        "new_cleanup_authorized", "records",
    }
    if set(decision) != allowed_decision:
        raise RecoveryError(f"decision fields must be exactly {sorted(allowed_decision)!r}")
    for field in ("operator", "decided_at", "rationale"):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            raise RecoveryError(f"decision.{field} is required")
    for field in (
        "same_principal_asserted", "old_client_id_owned", "old_cleanup_authorized",
        "new_client_id_owned", "new_cleanup_authorized",
    ):
        if decision.get(field) is not True:
            raise RecoveryError(f"decision.{field} must be explicitly true")
    records = decision.get("records")
    if not isinstance(records, list):
        raise RecoveryError("decision.records must be a list")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"delivery_id", "disposition"}:
            raise RecoveryError("each decision record needs delivery_id and disposition")
        delivery_id = record["delivery_id"]
        if not isinstance(delivery_id, str) or not delivery_id or delivery_id in seen:
            raise RecoveryError("decision delivery IDs must be unique nonempty strings")
        if record["disposition"] not in ALLOWED_DISPOSITIONS:
            raise RecoveryError("unsupported operator disposition")
        seen.add(delivery_id)

    result = json.loads(json.dumps(raw))
    result["source"], result["archive"], result["target"] = map(
        str, (source, archive, target),
    )
    result["broker"]["transport"] = "tcp"
    result["broker"]["tls"] = "plain"
    return result


def durable_identity(request: dict[str, Any], session_name: str) -> str:
    broker = request["broker"]
    session = request[session_name]
    protocol = "311" if broker["protocol"] == "mqtt311" else "5"
    username = broker.get("username")
    user = "none" if username is None else f"some:{len(username.encode())}:{username}"
    host, client_id = broker["host"], session["client_id"]
    return (
        f"moon-mqtt-durable-v2|{protocol}|resume|{session.get('session_expiry_secs', 0)}|"
        f"mqtt|{len(host.encode())}:{host}|{broker['port']}||"
        f"{len(client_id.encode())}:{client_id}|{user}"
    )


def validate_decisions(request: dict[str, Any], snapshot_value: dict[str, Any]) -> None:
    actual = {record["delivery_id"] for record in snapshot_value["records"]}
    decided = {record["delivery_id"] for record in request["decision"]["records"]}
    if actual != decided:
        raise RecoveryError(
            f"operator decisions must cover exactly the extant rows: actual={sorted(actual)!r} "
            f"decided={sorted(decided)!r}"
        )


JOURNAL_SQL = """
CREATE TABLE recovery_journal (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  schema_version INTEGER NOT NULL,
  operation_id TEXT NOT NULL,
  request_json BLOB NOT NULL,
  request_sha256 TEXT NOT NULL,
  archive_path TEXT NOT NULL,
  archive_sha256 TEXT NOT NULL,
  source_snapshot_sha256 TEXT NOT NULL,
  source_device INTEGER NOT NULL,
  source_inode INTEGER NOT NULL,
  old_identity TEXT NOT NULL,
  target_identity TEXT NOT NULL,
  retired_identity TEXT NOT NULL,
  phase TEXT NOT NULL,
  staged_target TEXT,
  staged_target_sha256 TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def journal_path(request: dict[str, Any]) -> Path:
    return Path(request["source"] + ".recovery.sqlite3")


def create_journal(
    request: dict[str, Any], archive_sha: str, source_snapshot: dict[str, Any],
    source_file: dict[str, int],
) -> None:
    final = journal_path(request)
    if final.exists():
        raise RecoveryError(f"recovery journal already exists: {final}")
    fd, name = tempfile.mkstemp(prefix=f".{final.name}.", suffix=".tmp", dir=final.parent)
    os.close(fd)
    staging = Path(name)
    os.chmod(staging, 0o600)
    request_bytes = canonical_json(request)
    timestamp = request["decision"]["decided_at"]
    old_identity = durable_identity(request, "old_session")
    target_identity = durable_identity(request, "new_session")
    retired = f"moon-mqtt-retired-v1|{request['operation_id']}|{archive_sha}"
    try:
        connection = sqlite3.connect(staging)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=EXTRA")
        connection.execute("PRAGMA user_version=1")
        connection.execute(JOURNAL_SQL)
        connection.execute(
            "INSERT INTO recovery_journal "
            "(singleton, schema_version, operation_id, request_json, request_sha256, "
            "archive_path, archive_sha256, source_snapshot_sha256, source_device, "
            "source_inode, old_identity, target_identity, retired_identity, phase, "
            "staged_target, staged_target_sha256, created_at, updated_at) VALUES "
            "(1, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Prepared', NULL, NULL, ?, ?)",
            (
                request["operation_id"], request_bytes,
                sha256_bytes(request_bytes), request["archive"], archive_sha,
                source_snapshot["snapshot_sha256"], source_file["device"],
                source_file["inode"], old_identity, target_identity, retired,
                timestamp, timestamp,
            ),
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RecoveryError("new recovery journal failed integrity_check")
        connection.close()
        fsync_file(staging)
        publish_file_no_replace(staging, final)
        os.chmod(final, 0o600)
    finally:
        staging.unlink(missing_ok=True)


def open_journal(request: dict[str, Any]) -> tuple[sqlite3.Connection, dict[str, Any]]:
    path = journal_path(request)
    connection = connect_sqlite(existing_path(str(path), "journal"), "rw")
    try:
        connection.execute("PRAGMA synchronous=EXTRA")
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 3:
            raise RecoveryError("recovery journal did not accept synchronous=EXTRA")
        value, _stored_request = validate_journal(connection)
        request_bytes = canonical_json(request)
        if bytes(value["request_json"]) != request_bytes:
            raise RecoveryError("request differs from the prepared recovery journal")
        if value["request_sha256"] != sha256_bytes(request_bytes):
            raise RecoveryError("journal request hash does not match")
        if value["phase"] not in PHASES:
            raise RecoveryError(f"unknown recovery phase: {value['phase']!r}")
        return connection, value
    except Exception:
        connection.close()
        raise


def validate_journal(
    connection: sqlite3.Connection,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if connection.execute("PRAGMA user_version").fetchone()[0] != JOURNAL_SCHEMA_VERSION:
        raise RecoveryError("unsupported recovery journal version")
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise RecoveryError("recovery journal failed integrity_check")
    objects = connection.execute(
        "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if objects != [("table", "recovery_journal")]:
        raise RecoveryError("recovery journal schema objects are invalid")
    columns = [row[1] for row in connection.execute("PRAGMA table_info(recovery_journal)")]
    if columns != EXPECTED_JOURNAL_COLUMNS:
        raise RecoveryError("recovery journal columns are invalid")
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM recovery_journal").fetchall()
    if len(rows) != 1 or rows[0]["singleton"] != 1:
        raise RecoveryError("recovery journal must contain exactly one row")
    value = dict(rows[0])
    if value["phase"] not in PHASES:
        raise RecoveryError(f"unknown recovery phase: {value['phase']!r}")
    try:
        request_raw = bytes(value["request_json"])
        stored_request = json.loads(request_raw)
    except (TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"recovery journal request is invalid: {error}") from error
    if canonical_json(stored_request) != request_raw:
        raise RecoveryError("recovery journal request is not canonical JSON")
    if sha256_bytes(request_raw) != value["request_sha256"]:
        raise RecoveryError("recovery journal request hash does not match")
    archive = existing_path(value["archive_path"], "journal archive")
    if sha256_file(archive) != value["archive_sha256"]:
        raise RecoveryError("journal archive hash does not match")
    return value, stored_request


def refresh_journal(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    return dict(connection.execute("SELECT * FROM recovery_journal WHERE singleton=1").fetchone())


def update_phase(
    connection: sqlite3.Connection,
    phase: str,
    *,
    staged_target: str | None = None,
    staged_sha: str | None = None,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise RecoveryError(f"invalid phase update: {phase}")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE recovery_journal SET phase=?, staged_target=coalesce(?, staged_target), "
            "staged_target_sha256=coalesce(?, staged_target_sha256), "
            "updated_at=? WHERE singleton=1",
            (phase, staged_target, staged_sha, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return refresh_journal(connection)


def snapshot_without_identity(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("identity", None)
    result.pop("snapshot_sha256", None)
    return result


def clean_broker_session(request: dict[str, Any], session_name: str) -> None:
    broker, session = request["broker"], request[session_name]
    protocol = paho.MQTTv311 if broker["protocol"] == "mqtt311" else paho.MQTTv5
    clean_session = True if protocol == paho.MQTTv311 else None
    client = paho.Client(
        paho.CallbackAPIVersion.VERSION2,
        client_id=session["client_id"],
        clean_session=clean_session,
        protocol=protocol,
        transport="tcp",
        reconnect_on_failure=False,
    )
    client.connect_timeout = float(broker.get("timeout_seconds", 5))
    if broker.get("username") is not None:
        password = None
        if broker.get("password_env") is not None:
            env_name = broker["password_env"]
            if env_name not in os.environ:
                raise RecoveryError(f"credential environment variable is missing: {env_name}")
            password = os.environ[env_name]
        client.username_pw_set(broker["username"], password=password)
    completed = threading.Event()
    result: dict[str, Any] = {}

    def on_connect(_client, _userdata, flags, reason_code, connack_properties):
        result["reason"] = reason_code
        result["session_present"] = bool(flags.session_present)
        result["server_session_expiry"] = (
            getattr(connack_properties, "SessionExpiryInterval", None)
            if connack_properties is not None else None
        )
        completed.set()

    def on_disconnect(_client, _userdata, _disconnect_flags, reason_code, _properties):
        if not completed.is_set():
            result["disconnect"] = reason_code
            completed.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    properties = None
    clean_start: Any = paho.MQTT_CLEAN_START_FIRST_ONLY
    if protocol == paho.MQTTv5:
        properties = Properties(PacketTypes.CONNECT)
        properties.SessionExpiryInterval = 0
        clean_start = True
    try:
        return_code = client.connect(
            broker["host"], broker["port"], keepalive=0,
            clean_start=clean_start, properties=properties,
        )
        if return_code != paho.MQTT_ERR_SUCCESS:
            raise RecoveryError(f"cleanup connect failed before CONNACK: {return_code}")
        client.loop_start()
        if not completed.wait(float(broker.get("timeout_seconds", 5))):
            raise RecoveryError("cleanup timed out waiting for CONNACK")
        reason = result.get("reason")
        if reason is None or reason.is_failure:
            raise RecoveryError(f"cleanup CONNACK was not accepted: {reason!s}")
        if result.get("session_present"):
            raise RecoveryError("cleanup CONNACK illegally retained Session Present")
        if result.get("server_session_expiry") not in (None, 0):
            raise RecoveryError("cleanup CONNACK returned a nonzero Session Expiry Interval")
        client.disconnect()
    except (OSError, ValueError) as error:
        raise RecoveryError(f"cleanup connection failed: {error}") from error
    finally:
        client.loop_stop()


META_SQL = """
CREATE TABLE durable_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  schema_version INTEGER NOT NULL,
  identity TEXT NOT NULL,
  next_sequence INTEGER NOT NULL CHECK (next_sequence > 0),
  known_session INTEGER NOT NULL CHECK (known_session IN (0, 1))
)
"""
OUTBOX_SQL = """
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
)
"""


def create_replacement(target: Path, target_identity: str) -> tuple[Path, str]:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.recovery-", suffix=".tmp", dir=target.parent,
    )
    path = Path(name)
    initial = os.fstat(descriptor)
    os.fchmod(descriptor, 0o600)
    try:
        connection = sqlite3.connect(path)
        try:
            opened = os.stat(path, follow_symlinks=False)
            if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
                raise RecoveryError("replacement staging path changed after secure creation")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute(META_SQL)
            connection.execute(OUTBOX_SQL)
            connection.execute(
                "INSERT INTO durable_meta VALUES (1, 2, ?, 1, 0)", (target_identity,),
            )
            connection.execute("PRAGMA user_version=2")
            connection.commit()
            require_schema(connection)
            if connection.execute("SELECT count(*) FROM durable_outbox").fetchone()[0] != 0:
                raise RecoveryError("new replacement is not empty")
        finally:
            connection.close()
        final_metadata = os.stat(path, follow_symlinks=False)
        if (final_metadata.st_dev, final_metadata.st_ino) != (initial.st_dev, initial.st_ino):
            raise RecoveryError("replacement staging path changed during construction")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path, sha256_file(path)


def verify_target(path: Path, identity: str, *, require_empty: bool) -> None:
    connection = connect_sqlite(existing_path(str(path), "target"), "ro")
    try:
        require_schema(connection)
        row = connection.execute(
            "SELECT identity, known_session FROM durable_meta WHERE singleton=1"
        ).fetchone()
        if row is None or row[0] != identity:
            raise RecoveryError("target identity does not match the recovery request")
        if require_empty and (
            row[1] != 0 or connection.execute("SELECT count(*) FROM durable_outbox").fetchone()[0]
        ):
            raise RecoveryError("target is not the expected empty replacement")
    finally:
        connection.close()


def prepare_resolution(
    request: dict[str, Any], source: sqlite3.Connection,
    source_snapshot: dict[str, Any], source_file: dict[str, int],
) -> None:
    archive = Path(request["archive"])
    old_identity = durable_identity(request, "old_session")
    if source_snapshot["identity"] != old_identity:
        raise RecoveryError("source identity does not match old_session")
    validate_decisions(request, source_snapshot)
    if archive.exists():
        manifest, archive_sha = verify_archive(archive, "resolution", request)
        if manifest.get("snapshot") != source_snapshot:
            raise RecoveryError("existing archive snapshot does not match source")
        if manifest.get("source_file") != source_file:
            raise RecoveryError("existing archive belongs to a different source file")
    else:
        archive_sha = build_archive(
            source, source_snapshot, archive, "resolution", request, source_file,
        )
        verify_archive(archive, "resolution", request)
        maybe_fail("after_archive_publish")
    current_file = os.stat(Path(request["source"]), follow_symlinks=False)
    if (current_file.st_dev, current_file.st_ino) != (
        source_file["device"], source_file["inode"],
    ):
        raise RecoveryError("source path changed before journal publication")
    create_journal(request, archive_sha, source_snapshot, source_file)
    maybe_fail("after_journal_publish")


def reconcile_source(
    request: dict[str, Any], journal: dict[str, Any], current: dict[str, Any],
    manifest: dict[str, Any], source_file: dict[str, int],
) -> None:
    expected_file = {
        "device": journal["source_device"], "inode": journal["source_inode"],
    }
    if source_file != expected_file or manifest.get("source_file") != expected_file:
        raise RecoveryError("source file identity differs from the prepared operation")
    archived = manifest["snapshot"]
    if snapshot_without_identity(current) != snapshot_without_identity(archived):
        raise RecoveryError("source rows/metadata changed after the recovery archive")
    allowed = {journal["old_identity"], journal["retired_identity"]}
    if current["identity"] not in allowed:
        raise RecoveryError("source identity is neither original nor this operation's tombstone")
    if journal["source_snapshot_sha256"] != archived["snapshot_sha256"]:
        raise RecoveryError("journal source fingerprint does not match archive")


def advance_resolution(
    request: dict[str, Any], source: sqlite3.Connection,
    source_snapshot: dict[str, Any], journal_connection: sqlite3.Connection,
    journal: dict[str, Any], manifest: dict[str, Any], source_file: dict[str, int],
) -> dict[str, Any]:
    reconcile_source(request, journal, source_snapshot, manifest, source_file)
    archive = Path(request["archive"])
    if sha256_file(archive) != journal["archive_sha256"]:
        raise RecoveryError("archive hash differs from the prepared journal")

    if journal["phase"] == "Prepared":
        if source_snapshot["identity"] == journal["old_identity"]:
            source.execute("BEGIN EXCLUSIVE")
            try:
                changed = source.execute(
                    "UPDATE durable_meta SET identity=? WHERE singleton=1 AND identity=?",
                    (journal["retired_identity"], journal["old_identity"]),
                ).rowcount
                if changed != 1:
                    raise RecoveryError("source identity retirement did not update one row")
                source.execute("COMMIT")
            except Exception:
                source.execute("ROLLBACK")
                raise
            maybe_fail("after_source_retire")
        journal = update_phase(journal_connection, "SourceRetired")
        source_snapshot = snapshot(source)
        reconcile_source(request, journal, source_snapshot, manifest, source_file)

    if journal["phase"] == "SourceRetired":
        journal = update_phase(journal_connection, "OldCleanRequested")
    if journal["phase"] == "OldCleanRequested":
        clean_broker_session(request, "old_session")
        maybe_fail("after_old_clean")
        journal = update_phase(journal_connection, "OldCleanConfirmed")
    if journal["phase"] == "OldCleanConfirmed":
        journal = update_phase(journal_connection, "NewCleanRequested")
    if journal["phase"] == "NewCleanRequested":
        clean_broker_session(request, "new_session")
        maybe_fail("after_new_clean")
        journal = update_phase(journal_connection, "NewCleanConfirmed")

    target = Path(request["target"])
    if journal["phase"] == "NewCleanConfirmed":
        if os.path.lexists(target):
            raise RecoveryError("target exists before replacement publication")
        stage, stage_sha = create_replacement(target, journal["target_identity"])
        journal = update_phase(
            journal_connection, "ReplacementReady",
            staged_target=str(stage), staged_sha=stage_sha,
        )
        maybe_fail("after_replacement_ready")

    if journal["phase"] == "ReplacementReady":
        recorded_stage = Path(journal["staged_target"])
        if recorded_stage.parent != target.parent or not recorded_stage.name.startswith(
            f".{target.name}.recovery-"
        ):
            raise RecoveryError("journal replacement staging path is invalid")
        if os.path.lexists(target):
            verify_target(target, journal["target_identity"], require_empty=True)
            if sha256_file(target) != journal["staged_target_sha256"]:
                raise RecoveryError("existing target is not this operation's staged replacement")
            if os.path.lexists(recorded_stage) and not os.path.samefile(recorded_stage, target):
                raise RecoveryError("published target is not linked to the recorded staging file")
        else:
            if os.path.lexists(recorded_stage):
                existing_path(str(recorded_stage), "replacement staging file")
            else:
                recorded_stage, stage_sha = create_replacement(
                    target, journal["target_identity"],
                )
                journal = update_phase(
                    journal_connection, "ReplacementReady",
                    staged_target=str(recorded_stage), staged_sha=stage_sha,
                )
            verify_target(recorded_stage, journal["target_identity"], require_empty=True)
            if sha256_file(recorded_stage) != journal["staged_target_sha256"]:
                raise RecoveryError("replacement staging file hash changed")
            try:
                os.link(recorded_stage, target)
            except FileExistsError:
                verify_target(target, journal["target_identity"], require_empty=True)
                if sha256_file(target) != journal["staged_target_sha256"]:
                    raise RecoveryError("target publication raced with another file")
            fsync_directory(target.parent)
            maybe_fail("after_target_publish")
        recorded_stage.unlink(missing_ok=True)
        fsync_directory(target.parent)
        journal = update_phase(journal_connection, "ReplacementPublished")

    if journal["phase"] == "ReplacementPublished":
        verify_target(target, journal["target_identity"], require_empty=False)
        journal = update_phase(journal_connection, "Complete")
    if journal["phase"] == "Complete":
        verify_target(target, journal["target_identity"], require_empty=False)
        return journal
    raise RecoveryError(f"recovery stopped in unexpected phase: {journal['phase']}")


def resolve(request_path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError(f"cannot read recovery request: {error}") from error
    request = validate_request(raw)
    if (
        not os.path.lexists(journal_path(request)) and
        os.path.lexists(Path(request["target"]))
    ):
        raise RecoveryError("target must be absent before a new recovery operation")
    source, source_snapshot, source_file = acquire_resolution_source(Path(request["source"]))
    journal_connection: sqlite3.Connection | None = None
    try:
        path = journal_path(request)
        if not path.exists():
            prepare_resolution(request, source, source_snapshot, source_file)
        journal_connection, journal = open_journal(request)
        manifest, archive_sha = verify_archive(
            existing_path(request["archive"], "archive"), "resolution", request,
        )
        if archive_sha != journal["archive_sha256"]:
            raise RecoveryError("archive bytes differ from the prepared journal")
        result = advance_resolution(
            request, source, source_snapshot, journal_connection, journal, manifest,
            source_file,
        )
        return {
            "operation_id": result["operation_id"], "phase": result["phase"],
            "source": request["source"], "archive": request["archive"],
            "target": request["target"],
        }
    finally:
        if journal_connection is not None:
            journal_connection.close()
        source.close()


def inspect_command(source_raw: str) -> dict[str, Any]:
    return observation_snapshot(existing_path(source_raw, "source"))


def export_command(source_raw: str, archive_raw: str) -> dict[str, Any]:
    source_path = existing_path(source_raw, "source")
    archive = prospective_path(archive_raw, "archive")
    if archive.exists():
        raise RecoveryError(f"refusing to replace existing archive: {archive}")
    if source_path == archive:
        raise RecoveryError("source and archive paths must be distinct")
    connection = connect_sqlite(source_path, "ro")
    source_metadata = os.stat(source_path, follow_symlinks=False)
    try:
        connection.execute("BEGIN")
        value = snapshot(connection)
        archive_sha = build_archive(
            connection, value, archive, "observation",
            source_file={"device": source_metadata.st_dev, "inode": source_metadata.st_ino},
        )
        connection.execute("COMMIT")
        verify_archive(archive, "observation")
    except sqlite3.Error as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise RecoveryError(f"cannot export durable source: {error}") from error
    finally:
        connection.close()
    return {"archive": str(archive), "sha256": archive_sha, "snapshot": value}


def status_command(source_raw: str) -> dict[str, Any]:
    source = existing_path(source_raw, "source")
    path = existing_path(str(source) + ".recovery.sqlite3", "journal")
    connection = connect_sqlite(path, "ro")
    try:
        connection.execute("BEGIN")
        row, request = validate_journal(connection)
        source_metadata = os.stat(source, follow_symlinks=False)
        if (row["source_device"], row["source_inode"]) != (
            source_metadata.st_dev, source_metadata.st_ino,
        ):
            raise RecoveryError("journal source file identity does not match")
        manifest, archive_sha = verify_archive(
            existing_path(row["archive_path"], "journal archive"),
            "resolution", request,
        )
        if archive_sha != row["archive_sha256"]:
            raise RecoveryError("journal archive bytes do not match")
        connection.execute("COMMIT")
        return {
            key: row[key] for key in (
                "operation_id", "phase", "archive_path", "archive_sha256",
                "staged_target", "staged_target_sha256", "created_at", "updated_at",
            )
        }
    except sqlite3.Error as error:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise RecoveryError(f"cannot read recovery journal: {error}") from error
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=REQUEST_EXAMPLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--source", required=True)
    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--source", required=True)
    export_parser.add_argument("--archive", required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--archive", required=True)
    for name in ("resolve", "resume"):
        command_parser = subparsers.add_parser(name)
        command_parser.add_argument("--request", required=True)
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--source", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "inspect":
            result = inspect_command(arguments.source)
        elif arguments.command == "export":
            result = export_command(arguments.source, arguments.archive)
        elif arguments.command == "verify":
            archive = existing_path(arguments.archive, "archive")
            manifest, digest = verify_archive(archive)
            result = {"archive": str(archive), "sha256": digest, "manifest": manifest}
        elif arguments.command in ("resolve", "resume"):
            result = resolve(existing_path(arguments.request, "request"))
        else:
            result = status_command(arguments.source)
        sys.stdout.buffer.write(canonical_json(result) + b"\n")
        return 0
    except RecoveryError as error:
        print(f"durable recovery refused: {error}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as error:
        # Filesystem failures may occur before a temporary file or journal even
        # exists. Preserve the nonzero result without an unstructured traceback;
        # do not retry, overwrite, or infer which side effects committed.
        print(f"durable recovery refused: storage failure: {error}; preserve files and inspect/status before resuming", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
