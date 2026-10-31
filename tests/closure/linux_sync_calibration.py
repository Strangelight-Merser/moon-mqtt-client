#!/usr/bin/env python3
"""Qualify the Linux observer on its actual dynamic libc call path."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

HERE=Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    assert platform.system()=='Linux'
    lib=out/'sync_observer.so';exe=out/'calibration'
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC',str(HERE/'linux_sync_observer.c'),'-ldl','-o',str(lib)],check=True)
    subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(HERE/'linux_sync_calibration.c'),'-o',str(exe)],check=True)
    report={'platform':platform.platform(),'diagnostic_only':True,'tests':[]}
    for name,delay,capacity,code in [('negative',0,100,0),('positive',50000000,100,0),('overflow',0,2,123),('output-failure',0,100,122),('flush-failure',0,100,122),('missing-path',0,100,124)]:
        work=out/name;work.mkdir();output=work/'sync.jsonl'
        if name=='output-failure':output.write_text('existing file must remain unchanged\n')
        env={**os.environ,'LD_PRELOAD':str(lib),'MQTT_SYNC_OUTPUT':str(output),'MQTT_SYNC_DELAY_NS':str(delay),'MQTT_SYNC_CAPACITY':str(capacity)}
        if name=='flush-failure':env['MQTT_SYNC_FAIL_FLUSH']='1'
        command=['/bin/true'] if name=='missing-path' else [str(exe)]
        run=subprocess.run(command,cwd=work,env=env,capture_output=True,text=True)
        assert run.returncode==code,(name,run.returncode,run.stderr)
        if name=='output-failure':
            assert output.read_text()=='existing file must remain unchanged\n'
        elif name=='flush-failure':
            assert output.stat().st_size==0
        elif name=='missing-path':
            assert json.loads(output.read_text())['total']==0
        else:
            data=[json.loads(x) for x in output.read_text().splitlines()];buffer=data[-1];calls=data[:-1]
            assert buffer['total']==6 and buffer['lost']==max(0,6-capacity)
            if capacity>=6:
                assert {r['call'] for r in calls}=={'fsync','fdatasync'}
                assert calls[0]['fd']==calls[2]['fd'] and calls[0]['inode']!=calls[2]['inode']
                assert calls[0]['path'].endswith('first.db') and calls[2]['path'].endswith('second.db')
                assert calls[3]['mode'] & 0o170000 == 0o040000
                assert all(r['scheduler_ok'] for r in calls)
                assert all(r['rc']==-1 and r['errno']==9 for r in calls[-2:])
                assert all(r['rc']==0 for r in calls[:4])
                assert calls[0]['errno']==calls[1]['errno']==7
                assert all(r['end_ns']-r['begin_ns']>=delay for r in calls)
                if delay==0:
                    assert max(r['end_ns']-r['begin_ns'] for r in calls)<50000000, 'negative control not distinguishable'
        report['tests'].append({'name':name,'passed':True,'returncode':run.returncode})
        (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
