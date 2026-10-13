"""Fixed eight-case, three-repeat external per-dial calibration."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
import threading

from dial_evidence import ROOT, Ledger, Endpoint, FirstPacket, build_observer, observed_env, correlate, require, digest
sys.path.insert(0, str(ROOT/'tests'))
sys.path.insert(0, str(ROOT/'scripts'))
from harness import NativeProcess, integration_fixture
from protocol_faults import recv_packet
from acceptance import source_identity
from observer_controls import check as observer_controls

CASES = ['normal','initial_refusal','recovery_refusal_then_success','tcp_refused',
         'tls_verification_failure','tls_timeout','tls_success_then_eof_before_connack','maintenance_to_ordinary']


def run_case(binary, library, pki, out, case):
    out.mkdir()
    ledger = Ledger()
    tls = case.startswith('tls_')
    contexts = None
    if tls and case != 'tls_timeout':
        contexts = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        path = pki
        contexts.load_cert_chain(path/'server.pem', path/'server.key')
        if case == 'tls_verification_failure':
            contexts.verify_mode = ssl.CERT_REQUIRED
            contexts.load_verify_locations(pki/'client-ca.pem')
    driver = None
    ready = threading.Event()
    def handler(conn, peer, endpoint):
        require(ready.wait(5), "native process was not started")
        number = int(peer.split('/')[-1])
        if case == 'tls_timeout':
            while conn.recv(4096):
                pass
            return
        packet = recv_packet(conn)
        FirstPacket(ledger,peer,'connect').feed(packet)
        if case == 'tls_success_then_eof_before_connack':
            return
        reason = 0x87 if case == 'initial_refusal' else 0x89 if (
            case == 'recovery_refusal_then_success' and number == 2 or
            case == 'maintenance_to_ordinary' and number == 3) else 0
        response = bytes([0x20,3,0,reason,0])
        FirstPacket(ledger,peer,'connack').feed(response)
        conn.sendall(response)
        if reason:
            return
        if case == 'recovery_refusal_then_success' and number == 1:
            driver.expect('connected 1')
            return
        if case == 'maintenance_to_ordinary' and number < 3:
            driver.expect('connected '+str(number))
            if number == 1:
                conn.sendall(b'\xe0\x01\x89')
                ledger.add('server_disconnect',peer=peer,reason=137,wire_hex='e00189')
            return
        require(recv_packet(conn) == b'\xe0\x00', 'normal close is not MQTT DISCONNECT')
    endpoint = None
    reserved = None
    if case == 'tcp_refused':
        reserved = socket.socket();reserved.bind(('127.0.0.1',0));port=reserved.getsockname()[1]
        reserved.close(); reserved = None
    else:
        endpoint = Endpoint(ledger, context=contexts, handler=handler);port=endpoint.port
    env = {**os.environ, **observed_env(library,binary,out/'sockets.jsonl'),
           'POLICY_PORT':str(port), 'POLICY_PROTOCOL':'5',
           'POLICY_TARGET':'3' if case=='maintenance_to_ordinary' else '2' if case=='recovery_refusal_then_success' else '1'}
    if tls:
        env['POLICY_CA']=str(pki/'ca.pem')
    if case == 'tls_verification_failure':
        env.update(POLICY_CERT=str(pki/'client-unrelated.pem'), POLICY_KEY=str(pki/'client-unrelated.key'))
    started=time.monotonic()
    try:
        driver=NativeProcess([binary],env=env)
        ready.set()
        result=driver.finish(timeout=16)
    finally:
        if driver:
            driver.close()
            (out/'client.log').write_text(''.join(driver.lines))
        if endpoint:
            endpoint.close()
        if reserved:
            reserved.close()
        ledger.save(out/'peer.jsonl')
    (out/'client.log').write_text(result.stdout)
    report=correlate(out/'sockets.jsonl',out/'peer.jsonl',driver.process.pid)
    report.update(case=case,binary_sha256=digest(binary),returncode=result.returncode,seconds=time.monotonic()-started,
                  client_lines=result.stdout.splitlines())
    reasons=[r['connack']['reason'] for r in report['attempts'] if r['connack']]
    expected={'normal':[0],'initial_refusal':[135],'recovery_refusal_then_success':[0,137,0],
              'maintenance_to_ordinary':[0,0,137,0]}.get(case,[])
    require(reasons==expected, f'{case}: CONNACK sequence {reasons}')
    events=ledger.rows
    if case=='tls_verification_failure':
        require(any(r['event']=='tls_alert' and r['direction']=='write' and r['code']==48 for r in events),'missing numeric unknown_CA alert')
        require(any(r['event']=='tls_failure' and r['verify_code']==20 for r in events),'missing numeric certificate verify failure')
    if case=='tls_success_then_eof_before_connack':
        require(any(r['event']=='tls_success' for r in events),'missing successful TLS handshake')
        require(any(r['event']=='connect' for r in events),'missing CONNECT before EOF')
    if case=='tls_timeout':
        require(report['seconds']>=1.8 and not any(r['event']=='connect' for r in events),'timeout did not reach intended pre-MQTT phase')
    if case=='tcp_refused':
        require(not any(r['tcp_success'] for r in report['attempts']),'unexpected TCP success')
        require(any(r['so_error'] in (61,111) or r['errno'] in (61,111) for a in report['attempts'] for r in a['syscalls']),'missing numeric ECONNREFUSED')
    if case in ('normal','initial_refusal','recovery_refusal_then_success','maintenance_to_ordinary'):
        require(result.returncode==0,'unexpected native exit: '+result.stdout)
    successes=[r for r in report['attempts'] if r['connack'] and r['connack']['reason']==0]
    connected=[int(line.split()[1]) for line in result.stdout.splitlines() if line.startswith('connected ')]
    if case in ('normal','recovery_refusal_then_success','maintenance_to_ordinary'):
        require(connected==list(range(1,len(successes)+1)),'generation/CONNACK conservation failed')
        for attempt,generation in zip(successes,connected):
            attempt['generation']=generation
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def negative_controls(report_dir, out):
    original=(report_dir/'sockets.jsonl').read_text(); peer=(report_dir/'peer.jsonl').read_text()
    pid=json.loads((report_dir/'report.json').read_text())['pid']
    directory=out/'negative-controls';directory.mkdir()
    (directory/'peer.jsonl').write_text(peer)
    rows=[json.loads(line) for line in original.splitlines()]
    for name in ('missing-record','overflow','wrong-pid','wrong-dial'):
        changed=copy.deepcopy(rows)
        if name=='missing-record': changed.pop(1)
        elif name=='overflow': changed[-1]['lost']=1
        elif name=='wrong-dial':
            next(r for r in changed if r.get('kind')==0)['dial']=999999
        else: changed[0]['pid']+=1
        target=directory/(name+'.jsonl');target.write_text(''.join(json.dumps(r)+'\n' for r in changed))
        try: correlate(target,directory/'peer.jsonl',pid)
        except ValueError: pass
        else: raise AssertionError('negative control accepted: '+name)
    result=json.loads((report_dir/'report.json').read_text())
    attempts=result['attempts']
    require(len({a['fd'] for a in attempts})<len(attempts),'calibration did not exercise FD reuse')
    require(len({a['life'] for a in attempts})==len(attempts),'FD reuse merged lifetimes')
    # Add a distinct, complete observer connection; it must not enter candidate attempts.
    p=[json.loads(line) for line in peer.splitlines()];footer=p.pop()
    extra={'index':len(p),'ns':0,'event':'accept','peer':'observer/1','boot':'observer','remote':['127.0.0.1',1],'local':['127.0.0.1',2]};p.append(extra)
    p.append({'index':len(p),'ns':1,'event':'close','peer':'observer/1'})
    footer['total']=len(p);p.append(footer)
    (directory/'observer.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in p))
    (directory/'valid.jsonl').write_text(original)
    joined=correlate(directory/'valid.jsonl',directory/'observer.jsonl',pid)
    require(joined['excluded_peer_connections']==1,'observer not excluded')
    return ['missing-record','overflow','wrong-pid','wrong-dial','fd-reuse','observer-excluded']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=source_identity();library=build_observer(out)
    subprocess.run([os.environ.get('MOON',str(ROOT/'scripts/moon.sh')),'build','--target','native'],cwd=ROOT,check=True)
    binary=out/'dial_driver.exe';shutil.copy2(next((ROOT/'_build/native/debug/build').glob('**/dial_driver/dial_driver.exe')),binary)
    pki=out/'pki';pki.mkdir();fixture=integration_fixture();fixture.write_pki(pki, mtls=True)
    summary={'source':source,'binary_sha256':digest(binary),'observer_sha256':digest(library),'diagnostic_only':True,'smoke':args.smoke,'cases':[],'complete':False}
    try:
        for repetition in range(1,2 if args.smoke else 4):
            for case in CASES:
                print('RUN',repetition,case,flush=True)
                summary['cases'].append(run_case(binary,library,pki,out/f'{repetition}-{case}',case))
        summary['observer_controls']=observer_controls(library,out)
        summary['negative_controls']=negative_controls(out/'1-maintenance_to_ordinary',out)
        require(source_identity()==source,'source changed during calibration')
        summary['complete']=True
    finally:
        (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('Calibration complete:',out)

if __name__=='__main__':main()
