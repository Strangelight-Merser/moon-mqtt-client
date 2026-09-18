#!/usr/bin/env python3
"""Raw RFC 6455 peer verifies native MQTT byte-stream adaptation and failures."""
from __future__ import annotations
import base64
import hashlib
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time
import unittest

from build_paths import demo_binary

ROOT = Path(__file__).resolve().parents[1]
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


def exact(conn, n):
    data = b''
    while len(data) < n:
        chunk = conn.recv(n - len(data))
        if not chunk:
            raise EOFError('peer closed')
        data += chunk
    return data


def frame(data, opcode=2, fin=True, masked=False):
    head = bytes([(0x80 if fin else 0) | opcode])
    length = len(data)
    flag = 0x80 if masked else 0
    if length < 126:
        head += bytes([flag | length])
    elif length < 65536:
        head += bytes([flag | 126]) + struct.pack('!H', length)
    else:
        head += bytes([flag | 127]) + struct.pack('!Q', length)
    if masked:
        mask = b'abcd'
        return head + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data))
    return head + data


class Peer:
    def __init__(self, conn):
        self.conn = conn
        self.mqtt = bytearray()
        self.pongs = []

    def upgrade(self, protocol='mqtt', accept=True, extra=b''):
        data = b''
        while not data.endswith(b'\r\n\r\n'):
            data += exact(self.conn, 1)
            assert len(data) <= 16384, 'unbounded request'
        lines = data.decode('ascii').split('\r\n')
        assert lines[0] == 'GET /mqtt HTTP/1.1', lines[0]
        headers = dict(line.split(': ', 1) for line in lines[1:] if line)
        lower = {k.lower(): v for k, v in headers.items()}
        assert lower['sec-websocket-protocol'] == 'mqtt'
        assert len(base64.b64decode(lower['sec-websocket-key'])) == 16
        key = base64.b64encode(hashlib.sha1((lower['sec-websocket-key'] + GUID).encode()).digest())
        response = b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + (key if accept else b'invalid') + b'\r\n'
        if protocol is not None:
            response += b'Sec-WebSocket-Protocol: ' + protocol.encode() + b'\r\n'
        self.conn.sendall(response + extra + b'\r\n')

    def read_frame(self):
        first, second = exact(self.conn, 2)
        assert second & 0x80, 'client must mask EVERY frame'
        size = second & 127
        if size == 126:
            size = struct.unpack('!H', exact(self.conn, 2))[0]
        elif size == 127:
            size = struct.unpack('!Q', exact(self.conn, 8))[0]
        assert size <= 65536, 'unbounded outbound frame'
        mask = exact(self.conn, 4)
        data = bytes(v ^ mask[i % 4] for i, v in enumerate(exact(self.conn, size)))
        return first & 15, data

    def packet(self):
        while True:
            if len(self.mqtt) >= 2:
                size, multiplier, index = 0, 1, 1
                while index < min(len(self.mqtt), 5):
                    digit = self.mqtt[index]
                    size += (digit & 127) * multiplier
                    multiplier *= 128
                    index += 1
                    if not digit & 128:
                        if len(self.mqtt) >= index + size:
                            packet = bytes(self.mqtt[:index + size])
                            del self.mqtt[:index + size]
                            return packet
                        break
            opcode, data = self.read_frame()
            if opcode == 10:
                self.pongs.append(data)
            else:
                assert opcode in (0, 2), f'nonbinary MQTT frame opcode={opcode}'
                self.mqtt.extend(data)

    def puback(self):
        packet = self.packet()
        assert packet[0] == 0x32, packet
        index = 1
        while packet[index] & 128:
            index += 1
        index += 1
        topic_len = int.from_bytes(packet[index:index + 2], 'big')
        pid = packet[index + 2 + topic_len:index + 4 + topic_len]
        return b'\x40\x02' + pid


class WsFaults(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([os.environ.get('MOON', str(ROOT / 'scripts/moon.sh')),
                        'build', '--target', 'native'], cwd=ROOT, check=True)
        cls.cli = demo_binary(ROOT, 'cli')

    def run_peer(self, handler, success):
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(12)
        errors = []
        def serve():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(10)
                    handler(Peer(conn))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        result = subprocess.run([str(self.cli), 'publish', '--host', '127.0.0.1',
                                 '--port', str(listener.getsockname()[1]), '--ws-path', '/mqtt',
                                 '--topic', 'it/ws/test', '--payload', 'hello', '--qos', '1'],
                                cwd=ROOT, capture_output=True, text=True, timeout=12)
        worker.join(timeout=12)
        self.assertFalse(worker.is_alive(), 'peer did not close')
        self.assertEqual(errors, [], f'peer errors: {errors}')
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"outcome":"sent"', result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('"outcome":"sent"', result.stdout)
        return result

    def test_binary_stream_crosses_frames_messages_and_idle_header(self):
        def peer(p):
            p.upgrade()
            self.assertEqual(p.packet()[0], 0x10)
            first = frame(b'\x20')
            p.conn.sendall(first[:1])
            time.sleep(.2)  # Cross the former 100 ms idle cancellation slice.
            p.conn.sendall(first[1:] + frame(b'\x02', fin=False))
            p.conn.sendall(frame(b'ws-ping', opcode=9))
            p.conn.sendall(frame(b'\x00\x00', opcode=0))
            ack = p.puback()
            p.conn.sendall(frame(ack[:2]) + frame(ack[2:] + b'\xd0\x00'))
            self.assertEqual(p.packet(), b'\xe0\x00')
            self.assertIn(b'ws-ping', p.pongs)
        self.run_peer(peer, True)

    def test_multiple_mqtt_packets_in_single_binary_message(self):
        def peer(p):
            p.upgrade()
            self.assertEqual(p.packet()[0], 0x10)
            p.conn.sendall(frame(b'\x20\x02\x00\x00\xd0\x00'))
            p.conn.sendall(frame(p.puback()))
            self.assertEqual(p.packet(), b'\xe0\x00')
        self.run_peer(peer, True)

    def test_rejects_invalid_upgrade(self):
        for kwargs in ({'protocol': None}, {'protocol': 'other'}, {'accept': False},
                       {'extra': b'Sec-WebSocket-Extensions: permessage-deflate\r\n'},
                       {'extra': b'X-Large: ' + b'a' * 32768 + b'\r\n'}):
            with self.subTest(kwargs=str(kwargs)[:80]):
                self.run_peer(lambda p: p.upgrade(**kwargs), False)

    def test_rejects_nonbinary_and_masked_server_frames(self):
        for kwargs in ({'opcode': 1}, {'masked': True}):
            with self.subTest(kwargs=kwargs):
                def peer(p):
                    p.upgrade()
                    p.packet()
                    p.conn.sendall(frame(b'\x20\x02\x00\x00', **kwargs))
                self.run_peer(peer, False)

    def test_mqtt_packet_limit_applies_inside_ws(self):
        def peer(p):
            p.upgrade()
            p.packet()
            p.conn.sendall(frame(b'\x30\xff\xff\x7f'))
        self.run_peer(peer, False)

    def test_publish_timeout_closes_partial_ws_read(self):
        def peer(p):
            p.upgrade()
            p.packet()
            p.conn.sendall(frame(b'\x20\x02\x00\x00'))
            p.puback()
            p.conn.sendall(b'\x82\x7e\x00')  # Partial extended frame header, never ACK.
            self.assertEqual(p.conn.recv(1), b'', 'client must close timed-out transport')
        started = time.monotonic()
        result = self.run_peer(peer, False)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn('"outcome":"unknown"', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
