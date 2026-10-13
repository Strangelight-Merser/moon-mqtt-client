"""Reanalyse preserved raw records, binding them to an accepted native binary.

This does not rerun acceptance, change the failed capture report, or confer a
Release pass on the decoder's newer source revision.
"""
import argparse
import json
from pathlib import Path
import subprocess

from dial_evidence import ROOT, digest, require
from dial_soak import audit_capture


def read(path):
    return json.loads(path.read_text())


def replay(capture, release):
    result = read(capture/'summary.json')
    original = read(capture/'dial-report.json')
    accepted = read(release/'result.json')
    source, end = read(release/'source.json'), read(release/'source-end.json')
    require(accepted['level']=='release' and accepted['full_level'] and accepted['successful']
            and accepted['source_unchanged'], 'reference is not a complete accepted Release')
    require(source == end and not source['dirty'], 'reference source was not frozen')
    require(source['commit'] == accepted['source_commit'] == original['source']['commit'], 'source revision mismatch')
    require(all(source[k] == original['source'][k] for k in ('tree','dirty','files')), 'capture source identity differs')
    parameters = result['parameters']
    require(parameters['duration_seconds']==1800 and parameters['disconnect_cycles']==100,
            'capture is not the registered full-duration run')
    transport = original['transport']
    require(transport in ('tcp','mtls'), 'unknown capture transport')
    formal_paths = list(release.glob(f'soak-{transport}-*/summary.json'))
    require(len(formal_paths)==1, 'ambiguous formal soak')
    formal_path = formal_paths[0]
    formal = read(formal_path)
    require(formal['gate']['passed'], 'formal soak gate failed')
    actual_hash = digest(capture/'soak_driver.exe')
    require(actual_hash == result['native_executable']['sha256'] == formal['native_executable']['sha256']
            == digest(formal_path.parent/'soak_driver.exe'), 'native executable bytes differ')
    observer = capture/'socket_observer.so'
    require(digest(observer)==original['observer_sha256'], 'observer library bytes differ')
    evidence, events = audit_capture(capture, result, transport=='mtls')
    inputs = [capture/name for name in ('summary.json','dial-report.json','sockets.jsonl','peer.jsonl',
                                      'client-events.json','soak_driver.exe','socket_observer.so')]
    inputs += [release/name for name in ('result.json','source.json','source-end.json')]
    inputs += [formal_path, formal_path.parent/'soak_driver.exe']
    return dict(complete=True, evidence_kind='offline_reanalysis_of_preserved_capture',
                measured_candidate=source['commit'], original_capture_complete=original['complete'],
                original_workflow_status_preserved=True, transport=transport,
                native_sha256=actual_hash, original_gate=result['gate'], client_events=events,
                decoder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                decoder_files={name:digest(Path(__file__).parent/name) for name in
                               ('dial_evidence.py','dial_soak.py','replay_dial_evidence.py')},
                inputs={str(path.resolve()):digest(path) for path in inputs}, evidence=evidence)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    report=replay(args.capture.resolve(), args.release.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        stream.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({key:report[key] for key in ('complete','measured_candidate','transport','native_sha256')}))
