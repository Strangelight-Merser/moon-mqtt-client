"""Run the unchanged soak assertions through an externally observed test gateway."""
import argparse
import json
import os
from pathlib import Path
import ssl
import sys

from dial_evidence import ROOT, Ledger, Endpoint, build_observer, observed_env, correlate, require, digest
sys.path.insert(0, str(ROOT/'tests'))
sys.path.insert(0, str(ROOT/'scripts'))
import soak
from acceptance import source_identity
from harness import free_port


def run(out, duration, cycles, mtls):
    out.mkdir(parents=True, exist_ok=False)
    source = source_identity()
    library = build_observer(out)
    ledger = Ledger()
    original_broker = soak.Broker

    class ObservedBroker(original_broker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.front_port = self.port
            self.backend_port = free_port()
            self.gateway = None
            self.context = None
            self.prepared = False

        def start(self):
            if not self.prepared:
                (out/'original-mosquitto.conf').write_bytes(self.config.read_bytes())
                if mtls:
                    self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    self.context.load_cert_chain(out/'server.pem', out/'server.key')
                    self.context.verify_mode = ssl.CERT_REQUIRED
                    self.context.load_verify_locations(out/'client-ca.pem')
                self.config = out/'gateway-backend.conf'
                self.config.write_text(f'listener {self.backend_port} 127.0.0.1\nallow_anonymous true\n'+self._log_directives())
                self.prepared = True
            self.port = self.backend_port
            try:
                super().start()
            finally:
                self.port = self.front_port
            self.gateway = Endpoint(ledger, port=self.front_port, context=self.context, backend=self.backend_port)
            ledger.add('broker_boot', boot=self.gateway.boot, broker_pid=self.process.pid, backend_port=self.backend_port)

        def stop(self):
            if self.gateway:
                self.gateway.close()
                self.gateway = None
            super().stop()

    env = observed_env(library, 'soak_driver.exe', out/'sockets.jsonl')
    previous = {key: os.environ.get(key) for key in env}
    report = {'complete': False, 'source': source, 'observer_sha256': digest(library),
              'diagnostic_only': True, 'transport': 'mtls' if mtls else 'tcp',
              'environment': 'external gateway; TLS terminates at test endpoint, then plain loopback Mosquitto'}
    try:
        os.environ.update(env)
        soak.Broker = ObservedBroker
        result = soak.run_soak(duration, cycles, 1024, 16, .2, out, mtls=mtls)
        require(result['gate']['passed'], 'original soak gate failed')
        native = result['native_executable']
        joined = correlate(out/'sockets.jsonl', save_peer(ledger, out), native['pid'])
        successes = [a for a in joined['attempts'] if a['connack'] and a['connack']['reason'] == 0]
        require(len(successes) == cycles+4, 'warmup/main CONNACK conservation failed')
        require(all(not a['connack'] or a['connack']['reason']==0 for a in joined['attempts']), 'unexpected broker refusal')
        events = json.loads((out/'client-events.json').read_text())
        expected_events = []
        generation = 0
        for index, dial in enumerate(joined['dials']):
            dial['scope'] = 'warmup' if index < 3 else 'main'
            if index < 3:
                require(dial['connack'] and dial['connack']['reason']==0, 'warmup dial was not successful')
                dial['generation'] = 1
                continue
            if dial['connack'] and dial['connack']['reason']==0:
                generation += 1
                expected_events.append(('connected',generation))
                if generation <= cycles:
                    expected_events.append(('disconnected',generation))
            else:
                require(generation > 0, 'initial main dial failed')
                expected_events.append(('disconnected',generation))
            dial['generation'] = generation
            for attempt in joined['attempts']:
                if attempt['dial']==dial['dial']:
                    attempt.update(scope=dial['scope'],generation=generation)
        actual_events = [(e['event'],e['generation']) for e in events]
        require(actual_events == expected_events, 'logical dial/client event conservation failed')
        require(generation == cycles+1, 'main generation conservation failed')
        if mtls:
            for attempt in joined['attempts']:
                if attempt['connack']:
                    tls = [e for e in attempt['events'] if e['event']=='tls_success']
                    require(len(tls)==1 and tls[0]['verified_client'] and tls[0]['certificate_sha256'], 'missing verified client handshake')
        require(source_identity()==source, 'source changed during observed soak')
        report.update(complete=True, native_executable=native, evidence=joined,
                      original_gate=result['gate'], client_events=events)
    finally:
        soak.Broker = original_broker
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        save_peer(ledger, out)
        (out/'dial-report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def save_peer(ledger, out):
    path=out/'peer.jsonl'; ledger.save(path); return path


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--duration', type=int, default=1800); parser.add_argument('--cycles', type=int, default=100)
    parser.add_argument('--mtls', action='store_true'); args=parser.parse_args()
    run(args.output.resolve(), args.duration, args.cycles, args.mtls)
    print('Observed soak complete:', args.output)
