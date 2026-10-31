"""Independent adversarial qualification of version 2 raw evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
import measurement_audit as audit


def rows(values, worker=0):
    tail = [dict(event='tail', phase='sample', worker=worker, sequence=i, elapsed_ns=v)
            for i,v in enumerate(values,1) if v >= 100000000]
    buckets = {}
    for v in values:
        if v < 100000000:
            upper = (v//10000+1)*10
            buckets[upper] = buckets.get(upper,0)+1
    common = {'phase':'sample','worker':worker}
    return [dict(common,event='range',first=1,last=len(values),outcome='completed'),
            dict(common,event='outcomes',first=1,last=len(values),accepted=len(values),completed=len(values),rejected=0,unknown=0,not_sent=0),
            dict(common,event='tail_buffer',version=2,capacity=4096,count=len(tail),lost=0),
            dict(common,event='overflow',lower_us=100000,count=len(tail),max_ns=max(values))] + tail + [dict(common,event='bucket',upper_us=u,count=c) for u,c in buckets.items()]


def result(t=100,p=10000000):
    return {'throughput_per_s':t,'measurement':{'quantiles':{'p99':{'ns':p}}}}


def pairs(t=100,p=10000000):
    return [{'off': result(), 'on':result(t,p)} for _ in range(3)]


class TailQualification(unittest.TestCase):
    def test_rank_boundary(self):
        for n, expected in [(99,10000),(98,160000001)]:
            r=audit.audit_phase(rows([1]*n+[160000001]*(100-n)),'sample')
            self.assertEqual(r['quantiles']['p99']['ns'],expected)
        self.assertEqual(audit.audit_phase(rows([99999999,100000000]),'sample')['overflow_count'],1)

    def test_int64_and_workers(self):
        value=2**63-1
        r=audit.audit_phase(rows([value])+rows([160000001],worker=1),'sample')
        self.assertEqual(r['quantiles']['p99'],{'rank':2,'tail_rank':2,'ns':value})

    def test_corruptions(self):
        base=rows([100000000,160000000])
        mutations=[lambda r:r.pop(),lambda r:r.append(copy.deepcopy(r[-1])),
                   lambda r:r[2].update(lost=1),lambda r:r[2].update(capacity=1),
                   lambda r:r[-1].update(elapsed_ns=2**63),lambda r:r[-1].update(elapsed_ns=99999999),
                   lambda r:r[-1].update(elapsed_ns=160000000.0),lambda r:r[-1].update(sequence=3),
                   lambda r:r[-1].update(worker=2),lambda r:r[1].update(completed=3),
                   lambda r:r.pop(2),lambda r:r[3].update(max_ns=170000000)]
        for i,mutate in enumerate(mutations):
            with self.subTest(i=i):
                r=copy.deepcopy(base);mutate(r)
                with self.assertRaises((ValueError,KeyError)):audit.audit_phase(r,'sample')

    def test_truncated_flush(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'native.jsonl';path.write_text('{"event":"tail"')
            with self.assertRaises(ValueError):audit.read_rows(path)

    def test_gate_boundaries(self):
        for t, p, expected in [(80,12500000,'within_trigger'),(79.999,12500000,'investigate'),(80.001,12499999,'within_trigger'),(100,12500001,'investigate')]:
            with self.subTest(t=t,p=p):
                self.assertEqual(audit.trace_comparison(pairs(t,p))['verdict'],expected)
        for delta,expected in [(14.99,'within_trigger'),(15.01,'indeterminate')]:
            # CV is delta/100 for [100-d*sqrt(1.5),100,100+d*sqrt(1.5)].
            data=pairs()
            for pair,value in zip(data,[100-delta*(1.5**.5),100,100+delta*(1.5**.5)]):
                pair['off']['throughput_per_s']=value
                pair['on']['throughput_per_s']=value
            self.assertEqual(audit.trace_comparison(data)['verdict'],expected)

    def test_zero_or_censored_rejected(self):
        for value in (0,None,float('nan'),float('inf'),True):
            data=pairs();data[0]['off']['measurement']['quantiles']['p99']['ns']=value
            with self.assertRaises(ValueError):audit.trace_comparison(data)


class TraceQualification(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'benchmark_driver.exe').write_bytes(b'explicit synthetic fixture')
        digest=audit.file_hash(self.root/'benchmark_driver.exe')
        self.source={'commit':'fixture','files':{'test':'fixture'}}
        self.report={'source_commit':'fixture','source_identity':self.source,
                     'measurement_protocol':{'version':2,'build':'debug-native','warmup':5,'sample':30,'pairs':3,'order':['off','on']},
                     'trace_ab':{'pairs':[]}}
        for i in range(1,4):
            pair={}
            for mode in ('off','on'):
                path=self.root/f'trace-ab-{i}-{mode}';path.mkdir()
                raw=rows([160000001]);warm=copy.deepcopy(raw)
                for r in warm:r['phase']='warmup'
                token=audit.hashlib.sha256(path.name.encode()).hexdigest()
                raw=[{'event':'measurement_identity','version':2,'token':token,'kind':'durable','trace_enabled':int(mode=='on'),'warmup':5,'sample':30}]+warm+raw+[{'event':'phase_start','phase':'sample','ns':1000000000},
                              {'event':'phase_end','phase':'sample','ns':31000000000}]
                (path/'native.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in raw))
                (path/'stderr.log').write_text('')
                if mode=='on':(path/'sqlite.jsonl').write_text('{}\n')
                r={'settings':{'name':'durable-stages','kind':'durable'},'native':{'pid':1,'sha256':digest},
                   'warmup_seconds':5,'sample_seconds':30,'actual_sample_seconds':30.0,
                   'completed':1,'throughput_per_s':1/30,'latency_overflow_count':1,'latency_max_ns':160000001,
                   'latency_us':dict.fromkeys(('p50','p95','p99'),{'lower_bound_us':100000,'upper_bound_us':None}),
                   'sqlite_trace_enabled':mode=='on','measurement':audit.audit_phase(raw,'sample')}
                (path/'result.json').write_text(json.dumps(r))
                (path/'identity.json').write_text(json.dumps({'version':2,'binary_sha256':digest,'token':token,'settings':r['settings'],
                    'warmup':5,'sample':30,'trace_enabled':mode=='on','trial':path.name}))
                pair[mode]=r
            self.report['trace_ab']['pairs'].append(pair)
        self.report['trace_ab']['comparison']=audit.trace_comparison(self.report['trace_ab']['pairs'])

    def tearDown(self):self.temp.cleanup()

    def test_raw_recomputation(self):
        self.assertEqual(audit.audit_trace(self.root,self.report,self.source)['verdict'],'within_trigger')

    def test_forged_verdict(self):
        self.report['trace_ab']['comparison']['verdict']='invented'
        with self.assertRaisesRegex(ValueError,'stored verdict'):audit.audit_trace(self.root,self.report,self.source)

    def test_summary_tamper(self):
        self.report['trace_ab']['pairs'][0]['off']['completed']=2
        with self.assertRaisesRegex(ValueError,'summary differs'):audit.audit_trace(self.root,self.report,self.source)

    def test_raw_tamper(self):
        path=self.root/'trace-ab-1-off/native.jsonl'
        path.write_text(path.read_text().replace('160000001','160000002'))
        with self.assertRaisesRegex(ValueError,'raw measurement'):audit.audit_trace(self.root,self.report,self.source)

    def test_origin_mismatch(self):
        path=self.root/'trace-ab-1-on/identity.json';r=json.loads(path.read_text());r['trial']='trace-ab-2-on';path.write_text(json.dumps(r))
        with self.assertRaisesRegex(ValueError,'trial identity'):audit.audit_trace(self.root,self.report,self.source)

    def test_raw_trial_swap(self):
        path=self.root/'trace-ab-1-off/native.jsonl'
        path.write_bytes((self.root/'trace-ab-2-off/native.jsonl').read_bytes())
        with self.assertRaisesRegex(ValueError,'raw trial identity'):audit.audit_trace(self.root,self.report,self.source)

    def test_binary_mismatch(self):
        (self.root/'benchmark_driver.exe').write_bytes(b'changed fixture')
        with self.assertRaisesRegex(ValueError,'identity'):audit.audit_trace(self.root,self.report,self.source)


if __name__=='__main__':unittest.main()
