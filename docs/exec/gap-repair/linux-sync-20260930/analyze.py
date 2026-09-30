import hashlib,json,sys,statistics,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[4]/'tests'))
from measurement_audit import audit_trial, trace_comparison
root=Path(sys.argv[1]);summary=json.loads((root/'summary.json').read_text())
inv=json.loads((root/'inventory.json').read_text())
for name,digest in inv.items():
    assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
print('Inventory verified:',len(inv),'files; status:',summary['status'],'commit:',summary['source']['commit'])
if summary['status']!='complete':
    print(json.dumps(summary,indent=2));sys.exit(1)
assert summary['source']['dirty'] is False
assert summary['build_mode']=='debug-native'
assert summary['protocol']=={'warmup':5,'sample':30,'order':['off1','on1','off2','on2','off3','on3']}
assert hashlib.sha256((root/'benchmark_driver.exe').read_bytes()).hexdigest()==summary['binary_sha256']
repo=Path(__file__).resolve().parents[4]
for name,info in summary['source']['files'].items():
    data=subprocess.check_output(['git','show',summary['source']['commit']+':'+name],cwd=repo)
    assert hashlib.sha256(data).hexdigest()==info['sha256'],name
print('Compiler:',summary['moon'])
rows=[];pairs=[{} for _ in range(3)]
def stats(values):
    values=sorted(values)
    return {'count':len(values),'sum':sum(values),'p99':values[max(0,__import__('math').ceil(.99*len(values))-1)],'max':max(values)} if values else None
for t in summary['trials']:
    name=t['mode']+str(t['repeat']);directory=root/name;r=t['result']
    audit_trial(directory,r)
    assert r==json.loads((directory/'result.json').read_text())
    assert r['native']['sha256']==summary['binary_sha256']
    native=[json.loads(x) for x in (directory/'native.jsonl').read_text().splitlines()]
    calls=[json.loads(x) for x in (directory/'sync.jsonl').read_text().splitlines()]
    buffer=calls.pop();assert buffer['lost']==0 and buffer['total']==len(calls)
    assert [x['index'] for x in calls]==list(range(len(calls)))
    starts=[x['ns'] for x in native if x['event']=='phase_start' and x['phase']=='sample']
    ends=[x['ns'] for x in native if x['event']=='phase_end' and x['phase']=='sample']
    calls=[x for x in calls if starts[0]<=x['begin_ns']<=ends[0]]
    assert all(x['end_ns']>=x['begin_ns'] and x['scheduler_ok'] and not x['identity_error'] for x in calls)
    stages=[x for x in native if x['event']=='stages' and x['id'].startswith('bench-sample-')]
    spans=[x['completed_ns']-x['begin_ns'] for x in stages]
    long=[]
    for stage in sorted(stages,key=lambda x:x['completed_ns']-x['begin_ns'],reverse=True)[:10]:
        a,b=stage['begin_ns'],stage['completed_ns']
        intersect=[c for c in calls if c['begin_ns']<b and c['end_ns']>a]
        long.append({'id':stage['id'],'stage_ns':b-a,'sync_wall_ns':sum(min(c['end_ns'],b)-max(c['begin_ns'],a) for c in intersect),
                     'sync_cpu_ns':sum(c['cpu_end_ns']-c['cpu_begin_ns'] for c in intersect),
                     'sync_runqueue_ns':sum(c['wait_end_ns']-c['wait_begin_ns'] for c in intersect),'sync_count':len(intersect)})
    row={'trial':name,'completed':r['completed'],'throughput':r['throughput_per_s'],'exact_p99_ns':r['measurement']['quantiles']['p99']['ns'],
         'overflow':r['latency_overflow_count'],'stage_ns':stats(spans),'sync_wall_ns':stats([c['end_ns']-c['begin_ns'] for c in calls]),
         'sync_cpu_ns':stats([c['cpu_end_ns']-c['cpu_begin_ns'] for c in calls]),'sync_runqueue_ns':stats([c['wait_end_ns']-c['wait_begin_ns'] for c in calls]),
         'call_names':sorted({c['call'] for c in calls}),'file_names':sorted({Path(c['path']).name for c in calls}),'longest':long}
    rows.append(row);pairs[t['repeat']-1][t['mode']]=r
analysis={'diagnostic_only':True,'inventory_files':len(inv),'trials':rows,'comparison':trace_comparison(pairs)}
(root.parent/'analysis.json').write_text(json.dumps(analysis,indent=2)+'\n')
print(json.dumps(analysis,indent=2))
