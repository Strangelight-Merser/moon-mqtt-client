#!/usr/bin/env python3
"""Fixed three-pair Linux diagnosis. Never substitutes for Release evidence."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'scripts'))
import benchmark
from acceptance import source_identity
from measurement_audit import file_hash, require


def proc_snapshot(pid=None):
    result={'ns':time.monotonic_ns(),'files':{},'gaps':{}}
    paths=[Path('/proc/pressure/io'),Path('/proc/diskstats'),Path('/proc/sys/kernel/sched_schedstats')]
    if pid is not None:
        paths.extend([Path(f'/proc/{pid}/io')])
        try:
            for thread in Path(f'/proc/{pid}/task').iterdir():
                paths.extend([thread/'stat',thread/'schedstat'])
        except OSError as error:
            result['gaps'][f'/proc/{pid}/task']=str(error)
    for path in paths:
        try:result['files'][str(path)]=path.read_text()
        except OSError as e:result['gaps'][str(path)]=str(e)
    return result


def counters(snapshot):
    result={}
    for path,text in snapshot['files'].items():
        if path=='/proc/diskstats':
            for line in text.splitlines():
                fields=line.split();result['disk:'+fields[2]]=[int(v) for v in fields[3:]]
        elif path=='/proc/pressure/io':
            for line in text.splitlines():
                fields=line.split();result['pressure:'+fields[0]]=[int(fields[-1].split('=')[1])]
        elif path.endswith('/schedstat'):
            result[path]=[int(v) for v in text.split()]
        elif path.endswith('/stat'):
            fields=text[text.rindex(')')+2:].split()
            result[path]=[int(fields[11]),int(fields[12])] # utime/stime ticks
        elif path.endswith('/io'):
            result[path]=[int(line.split(':')[1]) for line in text.splitlines()]
    return result


def deltas(samples):
    result=[]
    for before,after in zip(samples,samples[1:]):
        a,b=counters(before),counters(after)
        result.append({'begin_ns':before['ns'],'end_ns':after['ns'],
                       'counters':{k:[y-x for x,y in zip(a[k],b[k])] for k in a.keys() & b.keys()},
                       'appeared':sorted(b.keys()-a.keys()),'disappeared':sorted(a.keys()-b.keys())})
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();require(platform.system()=='Linux','Linux required')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=source_identity()
    moon=os.environ.get('MOON',str(ROOT/'scripts/moon.sh'))
    build=[moon,'build','--target','native']
    subprocess.run(build,cwd=ROOT,check=True)
    candidates=list((ROOT/'_build/native/debug/build').glob('**/benchmark_driver/benchmark_driver.exe'))
    require(len(candidates)==1,'ambiguous native benchmark')
    binary=out/'benchmark_driver.exe';shutil.copy2(candidates[0],binary)
    report={'diagnostic_only':True,'source':source,'binary_sha256':file_hash(binary),
            'platform':platform.platform(),'build':build,'moon':subprocess.check_output([moon,'version','--all'],cwd=ROOT,text=True),
            'build_mode':'debug-native','protocol':{'warmup':5,'sample':30,'order':['off1','on1','off2','on2','off3','on3']},
            'trials':[],'status':'started'}
    def save(): (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    try:
        subprocess.run([sys.executable,str(Path(__file__).with_name('linux_sync_calibration.py')),'--output',str(out/'calibration')],check=True)
        library=out/'calibration/sync_observer.so'
        for command,name in [(['ldd',str(binary)],'ldd.txt'),(['readelf','-Ws',str(binary)],'symbols.txt')]:
            run=subprocess.run(command,capture_output=True,text=True,check=True)
            (out/name).write_text(run.stdout+run.stderr)
        # Resolve actual dynamic references before interpreting interception coverage.
        require(any(x in (out/'symbols.txt').read_text() for x in ('fsync','fdatasync')), 'no dynamic sync path found; inspect linkage before running')
        settings=next(w for w in benchmark.WORKLOADS if w['name']=='durable-stages')
        for repeat in range(1,4):
            for mode in ('off','on'):
                directory=out/f'{mode}{repeat}';samples=[]
                def sample(pid):samples.append(proc_snapshot(pid))
                before=proc_snapshot();result=None
                try:
                    result=benchmark.trial(binary,settings,5,30,directory,trace_enabled=mode=='on',
                        native_env={'LD_PRELOAD':str(library),'MQTT_SYNC_OUTPUT':str(directory/'sync.jsonl'),
                                    'MQTT_SYNC_CAPACITY':'131072','MQTT_SYNC_DELAY_NS':'0'},diagnostic_sampler=sample)
                finally:
                    after=proc_snapshot()
                    (out/f'{mode}{repeat}-proc.json').write_text(json.dumps({'before':before,'samples':samples,'after':after,
                        'system_delta':deltas([before,after]),'process_deltas':deltas(samples),
                        'clock_ticks_per_second':os.sysconf('SC_CLK_TCK')},indent=2)+'\n')
                rows=[json.loads(x) for x in (directory/'sync.jsonl').read_text().splitlines()]
                buffer=rows[-1];calls=rows[:-1]
                require(buffer['event']=='buffer' and buffer['lost']==0 and buffer['total']==len(calls)>0,'sync coverage incomplete')
                require(all(r['scheduler_ok'] and not r['identity_error'] for r in calls),'sync attribution incomplete')
                require(samples and not any(s['gaps'] for s in [before,*samples,after]),'proc evidence incomplete')
                require(all(s['files']['/proc/sys/kernel/sched_schedstats'].strip()=='1' for s in [before,*samples,after]),'scheduler wait accounting disabled')
                report['trials'].append({'repeat':repeat,'mode':mode,'result':result,'sync_calls':len(calls),
                    'observed_calls':sorted({r['call'] for r in calls}),
                    'sync_wall_ns':benchmark.percentiles([r['end_ns']-r['begin_ns'] for r in calls]),
                    'sync_cpu_ns':benchmark.percentiles([r['cpu_end_ns']-r['cpu_begin_ns'] for r in calls]),
                    'runqueue_wait_ns':benchmark.percentiles([r['wait_end_ns']-r['wait_begin_ns'] for r in calls])})
                save()
        require(source_identity()==source,'source changed during diagnosis')
        report['status']='complete';report['interpretation']='Inspect per-call and stage timestamp overlap; system pressure is contextual, not process attribution.';save()
    except BaseException as error:
        report['status']='incomplete';report['error']=repr(error);save();raise
    finally:
        files={str(p.relative_to(out)):file_hash(p) for p in out.rglob('*') if p.is_file() and p.name!='inventory.json' and '.sqlite3' not in p.name}
        (out/'inventory.json').write_text(json.dumps(files,indent=2)+'\n')

if __name__=='__main__':main()
