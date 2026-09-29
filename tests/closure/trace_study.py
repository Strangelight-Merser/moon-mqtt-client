#!/usr/bin/env python3
"""Fixed four-condition diagnostic; does not replace the original E06 gate."""
import argparse, hashlib, json, os, platform, shutil, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
import benchmark

class BufferedObserver:
    def __init__(self, stream):
        self.stream=stream;self.rows=[];self.bytes=0
    def write(self,text):
        self.bytes+=len(text.encode())
        if self.bytes>32*1024*1024:raise RuntimeError('peer observer buffer overflow')
        self.rows.append(text)
    def close(self):
        self.stream.writelines(self.rows);self.stream.close()

OriginalPeer=benchmark.Peer
class DeferredPeer(OriginalPeer):
    def __init__(self,settings,directory):
        super().__init__(settings,directory)
        self.trace=BufferedObserver(self.trace)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--smoke',action='store_true');args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    protocol=json.loads((ROOT/'docs/exec/gap-repair/EXPERIMENT_PROTOCOL.json').read_text())['E06']
    orders=[['A','D']] if args.smoke else protocol['orders']
    subprocess.run([str(ROOT/'scripts/moon.sh'),'build','--target','native'],cwd=ROOT,check=True)
    candidates=list((ROOT/'_build/native/debug/build').glob('**/trace_driver/trace_driver.exe'));assert len(candidates)==1
    binary=out/'trace_driver.exe';shutil.copy2(candidates[0],binary)
    report={'diagnostic_only':True,'smoke':args.smoke,'protocol':protocol,'orders':orders,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'library_source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'library_changed':subprocess.check_output(['git','diff','--name-only','5ee6912f','--','*.mbt','*.c'],cwd=ROOT,text=True).splitlines(),'platform':platform.platform(),'driver_sources':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'tests/closure/trace_driver').glob('*') if p.is_file()},'trials':[]}
    def save(): (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    try:
        for n,order in enumerate(orders,1):
            for condition in order:
                print(f'RUN {n} {condition}',flush=True)
                directory=out/f'{n}-{condition}'
                deferred=condition in ['C','D'];trace=condition in ['B','D']
                benchmark.Peer=DeferredPeer if deferred else OriginalPeer
                os.environ['MQTT_BENCH_DEFER_OBSERVER']='1' if deferred else '0'
                os.environ['MQTT_BENCH_DEFER_TRACE']='1' if condition=='D' else '0'
                os.environ['MQTT_CLOSURE_CALLBACK_REPORT']=str(directory/'callbacks.json')
                before={'load':os.getloadavg(),'at_ns':time.monotonic_ns()}
                result=benchmark.trial(binary,next(w for w in benchmark.WORKLOADS if w['name']=='durable-stages'),0 if args.smoke else 5,1 if args.smoke else 30,directory,trace_enabled=trace)
                native=[json.loads(x) for x in (directory/'native.jsonl').read_text().splitlines()]
                costs=[x['return_ns']-x['enter_ns'] for x in native if x['event']=='observer_cost']
                buffer=[x for x in native if x['event']=='observer_buffer'];assert buffer and all(x['lost']==0 for x in buffer)
                callbacks=json.loads((directory/'callbacks.json').read_text());assert callbacks['lost']==0
                assert result['outcomes']['accepted']==result['completed']
                report['trials'].append({'condition':condition,'repeat':n,'environment_before':before,'result':result,'stage_log_cost_ns':benchmark.percentiles(costs),'observer_records':len(costs),'callback_cost':callbacks})
                save()
        report['status']='complete';save()
    except BaseException as error:
        report['status']='failed';report['error']=repr(error);save();raise
    finally:benchmark.Peer=OriginalPeer

if __name__=='__main__':main()
