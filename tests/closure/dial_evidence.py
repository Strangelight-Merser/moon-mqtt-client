"""External socket and test-endpoint evidence; never modifies candidate sources."""
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import socket
import ssl
import subprocess
import threading
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_observer(out):
    out.mkdir(parents=True, exist_ok=True)
    library = out / ('socket_observer.dylib' if platform.system() == 'Darwin' else 'socket_observer.so')
    options = ['-dynamiclib'] if platform.system() == 'Darwin' else ['-shared', '-fPIC', '-ldl', '-pthread']
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', str(HERE / 'socket_observer.c'), *options, '-o', str(library)], check=True)
    return library


def observed_env(library, binary, output):
    key = 'DYLD_INSERT_LIBRARIES' if platform.system() == 'Darwin' else 'LD_PRELOAD'
    require(not os.environ.get(key), 'do not replace another process interposer')
    return {key: str(library.resolve()), 'MQTT_DIAL_EXECUTABLE': Path(binary).name,
            'MQTT_DIAL_OUTPUT': str(output.resolve())}


class Ledger:
    def __init__(self, capacity=20000):
        self.rows = []
        self.capacity = capacity
        self.total = 0
        self.lock = threading.Lock()

    def add(self, event, **fields):
        with self.lock:
            index = self.total
            self.total += 1
            if index < self.capacity:
                self.rows.append({'index': index, 'ns': time.monotonic_ns(), 'event': event, **fields})

    def save(self, path):
        with self.lock:
            data = [*self.rows, {'event': 'buffer', 'total': self.total, 'lost': self.total-len(self.rows)}]
        path.write_text(''.join(json.dumps(r) + '\n' for r in data))
        require(data[-1]['lost'] == 0, 'gateway evidence overflow')


class FirstPacket:
    """Only the first MQTT frame is retained; credentials/payloads are never logged."""
    def __init__(self, ledger, peer, direction):
        self.ledger, self.peer, self.direction = ledger, peer, direction
        self.buffer = bytearray()
        self.done = False

    def feed(self, data):
        if self.done:
            return
        self.buffer.extend(data)
        require(len(self.buffer) <= 65536, 'first packet exceeds bounded evidence buffer')
        if len(self.buffer) < 2:
            return
        remaining, multiplier, offset = 0, 1, 1
        while offset < len(self.buffer) and offset <= 4:
            byte = self.buffer[offset]
            remaining += (byte & 127) * multiplier
            offset += 1
            if byte < 128:
                break
            multiplier *= 128
        else:
            require(offset <= 4, 'invalid MQTT remaining length')
            return
        if len(self.buffer) < offset + remaining:
            return
        packet = bytes(self.buffer[:offset+remaining]); body = packet[offset:]
        if self.direction == 'connect':
            require(packet[0] == 0x10 and len(body) >= 10, 'first client frame is not CONNECT')
            level = body[6]
            self.ledger.add('connect', peer=self.peer, protocol=level, flags=body[7], packet_bytes=len(packet))
        else:
            require(packet[0] == 0x20 and len(body) >= 2, 'first broker frame is not CONNACK')
            self.ledger.add('connack', peer=self.peer, session_present=body[0] & 1, reason=body[1], wire_hex=packet.hex())
        self.buffer.clear()
        self.done = True


class Endpoint:
    def __init__(self, ledger, port=0, context=None, handler=None, backend=None):
        self.ledger, self.context, self.handler, self.backend = ledger, context, handler, backend
        self.boot = uuid.uuid4().hex
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', port)); self.listener.listen(); self.listener.settimeout(.2)
        self.port = self.listener.getsockname()[1]
        self.stopping = False
        self.connections = set()
        self.threads = []
        self.errors = []
        self.fd_peers = {}
        self.next_id = 0
        self.lock = threading.Lock()
        if context:
            context._msg_callback = self.tls_message
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def tls_message(self, sock, direction, version, content, kind, data):
        peer = self.fd_peers[sock.fileno()]
        if int(content) == 21:
            self.ledger.add('tls_alert', peer=peer, direction=direction, version=int(version), code=int(kind), wire_hex=data.hex())
        if int(content) == 22 and int(kind) == 20:
            self.ledger.add('tls_finished', peer=peer, direction=direction, version=int(version), handshake_type=int(kind))

    def accept(self):
        while not self.stopping:
            try:
                conn, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.stopping:
                    self.errors.append('accept failed')
                break
            with self.lock:
                self.next_id += 1
                peer = self.boot + '/' + str(self.next_id)
                self.connections.add(conn)
                self.fd_peers[conn.fileno()] = peer
            self.ledger.add('accept', peer=peer, boot=self.boot, remote=list(address), local=list(conn.getsockname()))
            worker = threading.Thread(target=self.serve, args=(conn, peer), daemon=True)
            self.threads.append(worker); worker.start()

    def serve(self, raw, peer):
        conn = raw
        fd = raw.fileno()
        backend = None
        try:
            conn.settimeout(3)
            if self.context:
                conn = self.context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
                with self.lock:
                    self.connections.discard(raw); self.connections.add(conn)
                self.ledger.add('tls_start', peer=peer)
                try:
                    conn.do_handshake()
                except ssl.SSLError as error:
                    self.ledger.add('tls_failure', peer=peer, ssl_errno=error.errno,
                                    verify_code=getattr(error, 'verify_code', None))
                    return
                self.ledger.add('tls_success', peer=peer, version=conn.version(),
                                verify_mode=int(self.context.verify_mode),
                                verified_client=self.context.verify_mode == ssl.CERT_REQUIRED,
                                certificate_sha256=hashlib.sha256(conn.getpeercert(binary_form=True)).hexdigest() if conn.getpeercert(binary_form=True) else None)
            if self.handler:
                self.handler(conn, peer, self)
                return
            backend = socket.create_connection(('127.0.0.1', self.backend), timeout=3)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            backend.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            first = {conn: FirstPacket(self.ledger, peer, 'connect'), backend: FirstPacket(self.ledger, peer, 'connack')}
            while not self.stopping:
                ready, _, _ = select.select([conn, backend], [], [], .2)
                if isinstance(conn, ssl.SSLSocket) and conn.pending() and conn not in ready:
                    ready.append(conn)
                for source in ready:
                    data = source.recv(16384)
                    if not data:
                        return
                    first[source].feed(data)
                    (backend if source is conn else conn).sendall(data)
        except (OSError, EOFError) as error:
            # Close/reset during injected outages is expected and retained.
            self.ledger.add('transport_end', peer=peer, errno=getattr(error, 'errno', None), error_type=type(error).__name__)
        except BaseException as error:
            self.errors.append(repr(error))
        finally:
            if backend:
                backend.close()
            with self.lock:
                self.connections.discard(conn); self.connections.discard(raw); self.fd_peers.pop(fd, None)
            conn.close()
            raw.close()
            self.ledger.add('close', peer=peer)

    def close(self):
        self.stopping = True
        self.listener.close()
        with self.lock:
            for conn in list(self.connections):
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        self.thread.join(4)
        for worker in self.threads:
            worker.join(4)
        require(not self.thread.is_alive() and not any(t.is_alive() for t in self.threads), 'gateway worker did not stop')
        require(not self.errors, repr(self.errors))


def read_buffer(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    require(rows and rows[-1].get('event') == 'buffer', 'missing buffer footer')
    footer = rows.pop()
    require(footer['lost'] == 0 and footer['total'] == len(rows), 'missing or overflowed evidence')
    require([r['index'] for r in rows] == list(range(len(rows))), 'record sequence gap')
    return rows, footer


def correlate(socket_path, peer_path, pid):
    sockets, footer = read_buffer(socket_path)
    peers, _ = read_buffer(peer_path)
    require(footer['pid'] == pid and all(r['pid'] == pid for r in sockets), 'process identity mismatch')
    lives = {}
    resolutions = {}
    for row in sockets:
        if row['kind'] in (3,4):
            resolutions.setdefault(row['dial'], []).append(row)
            continue
        lives.setdefault(row['life'], []).append(row)
    require(resolutions, 'missing resolver lifetimes')
    for dial, rows in resolutions.items():
        require(dial > 0 and [r['kind'] for r in rows] == [3,4], 'incomplete resolver lifetime')
    accepts = [r for r in peers if r['event'] == 'accept']
    matched = set(); result = []
    for life, rows in lives.items():
        require(rows[0]['kind'] == 0 and rows[-1]['kind'] == 2, 'socket lifetime lacks connect/close')
        require(sum(r['kind']==0 for r in rows) == 1 and sum(r['kind']==2 for r in rows) == 1, 'ambiguous socket lifetime')
        require(len({r['fd'] for r in rows}) == 1, 'socket FD changed within lifetime')
        dial = rows[0]['dial']
        require(dial in resolutions and all(r['dial']==dial for r in rows), 'missing or ambiguous dial ownership')
        require(resolutions[dial][0]['index'] < rows[0]['index'] < resolutions[dial][1]['index'], 'connect outside resolver lifetime')
        success = rows[0]['rc'] == 0 or any(r['kind']==1 and r['rc']==0 and r['so_error']==0 for r in rows)
        endpoints = {tuple(r['local']) for r in rows if r['local'][1]}
        candidates = [p for p in accepts if tuple(p['remote']) in endpoints and p['local']==rows[0]['remote']]
        require(len(candidates) == (1 if success else 0), 'missing or ambiguous TCP-to-peer match')
        peer = candidates[0]['peer'] if candidates else None
        require(peer is None or peer not in matched, 'peer reused for another socket lifetime')
        if peer:
            matched.add(peer)
        events = [r for r in peers if r.get('peer') == peer] if peer else []
        require(not peer or sum(r['event']=='close' for r in events)==1, 'peer close record missing')
        connacks = [r for r in events if r['event']=='connack']
        connects = [r for r in events if r['event']=='connect']
        require(len(connacks)<=1 and len(connects)<=1, 'duplicate MQTT stage')
        require(not connacks or connects, 'CONNACK without CONNECT')
        result.append({'life':life,'dial':dial,'fd':rows[0]['fd'],'tcp_success':success,'syscalls':rows,
                       'peer':peer,'events':events,'connack':connacks[0] if connacks else None})
    dials = []
    for dial, resolution in resolutions.items():
        attempts = [a for a in result if a['dial']==dial]
        connacks = [a['connack'] for a in attempts if a['connack']]
        require(len(connacks)<=1, 'multiple CONNACKs in one logical dial')
        require(attempts or resolution[0]['rc'] != 0, 'successful resolver without socket attempt')
        dials.append({'dial':dial, 'socket_lives':[a['life'] for a in attempts],
                      'resolution':resolution, 'connack':connacks[0] if connacks else None})
    return {'pid':pid,'dials':dials,'resolutions':resolutions,'attempts':result,'excluded_peer_connections':len(accepts)-len(matched),
            'matched_peer_connections':len(matched),'complete':True}
