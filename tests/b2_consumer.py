#!/usr/bin/env python3
"""B2-B preparation: native candidate consumer and wire evidence, no hardware."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
from durable_outbox import packet, publish_id, body_offset
from harness import stop_process

ROOT = Path(__file__).resolve().parents[1]


class B2Consumer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            [
                os.environ.get("MOON", str(ROOT / "scripts/moon.sh")),
                "build",
                "--target",
                "native",
            ],
            cwd=ROOT,
            check=True,
        )
        cls.driver = next(
            (ROOT / "_build/native/debug/build").glob(
                "**/durable_consumer/durable_consumer.exe"
            )
        )

    def test_candidate_command_survives_process_crash_with_same_envelope(self):
        with (
            tempfile.TemporaryDirectory(prefix="moon-b2-consumer-") as temp,
            socket.socket() as listener,
        ):
            db = Path(temp) / "outbox.sqlite3"
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(8)
            envelope = {
                "id": "b2-host-1",
                "target": "ON",
                "expires_at_ms": int(time.time() * 1000) + 30000,
            }
            env = {
                **os.environ,
                "B2_HOST": "127.0.0.1",
                "B2_PORT": str(listener.getsockname()[1]),
                "B2_CLIENT_ID": "moon-b2-prepare",
                "B2_DB": str(db),
                "B2_ID": envelope["id"],
                "B2_ENVELOPE": json.dumps(envelope),
                "B2_PREFIX": "b2/private",
                "MOONBIT_ASYNC_CHECK_FD_LEAK": "1",
            }
            for name in (
                "B2_CA",
                "B2_CERT",
                "B2_KEY",
                "B2_USERNAME",
                "B2_PASSWORD",
                "B2_MQTT5",
            ):
                env.pop(name, None)
            wires = []
            logs = []
            for iteration in range(2):
                process = subprocess.Popen(
                    [str(self.driver)],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    with listener.accept()[0] as conn:
                        conn.settimeout(8)
                        self.assertEqual(packet(conn)[0], 0x10)
                        conn.sendall(b"\x20\x02" + bytes([iteration, 0]))
                        wire = packet(conn)
                        wires.append(wire)
                        offset = body_offset(wire)
                        n = int.from_bytes(wire[offset : offset + 2], "big")
                        self.assertEqual(
                            wire[offset + 2 : offset + 2 + n], b"b2/private/device/set"
                        )
                        self.assertEqual(json.loads(wire[offset + 4 + n :]), envelope)
                        if iteration == 0:
                            process.kill()
                        else:
                            conn.sendall(
                                b"\x40\x02" + publish_id(wire).to_bytes(2, "big")
                            )
                            self.assertEqual(packet(conn), b"\xe0\0")
                    out, err = process.communicate(timeout=8)
                    logs.append(out + err)
                    self.assertEqual(
                        process.returncode, -9 if iteration == 0 else 0, out + err
                    )
                finally:
                    stop_process(process)
                    process.stdout.close()
                    process.stderr.close()
            self.assertFalse(wires[0][0] & 8)
            self.assertTrue(wires[1][0] & 8)
            self.assertEqual(publish_id(wires[0]), publish_id(wires[1]))
            self.assertIn('"outcome":"broker_acknowledged"', logs[1])
            self.assertIn("requires_independent_observation", logs[1])
            with sqlite3.connect(db) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM durable_outbox"
                    ).fetchone()[0],
                    0,
                )
            destination = os.environ.get("MQTT_EVIDENCE_DIR")
            if destination:
                Path(destination, "b2-consumer.json").write_text(
                    json.dumps(
                        {
                            "wire_hex": [w.hex() for w in wires],
                            "logs": logs,
                            "physical": "evidence_insufficient",
                        },
                        indent=2,
                    )
                    + "\n"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
