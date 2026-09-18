#!/usr/bin/env python3
"""Native CLI WS/WSS interoperability against pinned EMQX and a TCP Paho oracle."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import unittest
import uuid

import paho.mqtt.client as paho
from build_paths import demo_binary
from ws_broker import WsBroker

ROOT = Path(__file__).resolve().parents[1]
MOON = os.environ.get('MOON', str(ROOT / 'scripts/moon.sh'))


class WsInterop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([MOON, 'build', '--target', 'native'], cwd=ROOT, check=True)
        cls.cli = demo_binary(ROOT, 'cli')
        cls.broker = WsBroker()
        cls.addClassCleanup(cls.broker.close)
        cls.broker.start()

    def command(self, mode, *, secure=False, identity=True, ca='ca.pem', **values):
        b = self.broker
        args = [str(self.cli), mode, '--host', 'localhost', '--port',
                str(b.wss_port if secure else b.ws_port), '--ws-path', '/mqtt',
                '--client-id', 'native-ws-' + uuid.uuid4().hex]
        if secure:
            args += ['--tls', '--ca', str(b.temp / ca)]
            if identity:
                args += ['--cert', str(b.temp / 'client.pem'), '--key', str(b.temp / 'client.key')]
        for key, value in values.items():
            args += ['--' + key.replace('_', '-'), str(value)]
        return args

    def oracle(self, topic):
        received = queue.Queue()
        ready = threading.Event()
        client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id='oracle-' + uuid.uuid4().hex)
        client.on_connect = lambda c, u, f, r, p: c.subscribe(topic, qos=1)
        client.on_subscribe = lambda *args: ready.set()
        client.on_message = lambda c, u, m: received.put((m.topic, m.payload, m.qos))
        self.addCleanup(client.loop_stop)
        self.addCleanup(client.disconnect)
        client.connect('127.0.0.1', self.broker.tcp_port)
        client.loop_start()
        self.assertTrue(ready.wait(5), 'oracle SUBACK')
        return client, received

    def roundtrip(self, secure):
        topic = 'it/ws/' + uuid.uuid4().hex
        oracle, received = self.oracle(topic)
        for qos in (0, 1):
            payload = f'native-{qos}-' + 'x' * 8192
            result = subprocess.run(self.command('publish', secure=secure, topic=topic,
                                    payload=payload, qos=qos), cwd=ROOT,
                                    text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"outcome":"sent"', result.stdout)
            self.assertEqual(received.get(timeout=5), (topic, payload.encode(), qos))
        proc = subprocess.Popen(self.command('subscribe', secure=secure, topic=topic,
                                qos=1, count=1, timeout_ms=5000), cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
        self.addCleanup(cleanup)
        lines = queue.Queue()
        def drain():
            for line in proc.stdout:
                lines.put(line)
        worker = threading.Thread(target=drain, daemon=True)
        worker.start()
        while True:
            line = lines.get(timeout=10)
            if json.loads(line).get('event') == 'subscribed':
                break
        oracle.publish(topic, b'oracle-to-native', qos=1).wait_for_publish(timeout=5)
        events = []
        while True:
            event = json.loads(lines.get(timeout=10))
            events.append(event)
            if event.get('event') == 'message':
                self.assertEqual(event['payload'], 'oracle-to-native')
                self.assertEqual(event['qos'], '1')
                break
        self.assertEqual(proc.wait(timeout=10), 0, proc.stderr.read())
        worker.join(timeout=2)

    def test_ws_native_qos0_qos1_both_directions(self):
        self.roundtrip(False)

    def test_wss_mtls_native_qos0_qos1_both_directions(self):
        self.roundtrip(True)

    def test_wss_rejects_missing_identity(self):
        result = subprocess.run(self.command('publish', secure=True, identity=False,
                                topic='it/ws/reject', payload='must-not-arrive', qos=1),
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('"outcome":"sent"', result.stdout)

    def test_wss_verifies_server_ca(self):
        result = subprocess.run(self.command('publish', secure=True, ca='unrelated-ca.pem',
                                topic='it/ws/reject', payload='must-not-arrive', qos=1),
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('"outcome":"sent"', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
