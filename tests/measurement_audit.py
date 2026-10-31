"""Version 2 exact overflow evidence; legacy histogram endpoints stay unchanged."""
import hashlib
import json
import math
from pathlib import Path

VERSION = 2

def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum=0, maximum=2**63-1):
    require(type(value) is int and minimum <= value <= maximum, f'invalid integer: {value!r}')
    return value


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit_phase(rows, phase):
    rows = [r for r in rows if r.get('phase') == phase]
    def unique(event):
        selected = [r for r in rows if r['event'] == event]
        keys = [integer(r['worker']) for r in selected]
        require(len(keys) == len(set(keys)), f'duplicate {event}')
        return dict(zip(keys, selected))
    outcomes, buffers, overflows = (unique(e) for e in ('outcomes', 'tail_buffer', 'overflow'))
    require(outcomes and outcomes.keys() == buffers.keys() == overflows.keys(), 'missing worker evidence')
    tails, histogram, completed = [], {}, 0
    for worker, outcome in outcomes.items():
        count = integer(outcome['completed'])
        completed += count
        buffer, overflow = buffers[worker], overflows[worker]
        require(buffer['version'] == VERSION, 'measurement version mismatch')
        require(integer(buffer['lost']) == 0, 'tail buffer lost samples')
        integer(buffer['capacity'], 1)
        require(overflow['lower_us'] == 100000, 'overflow boundary changed')
        integer(overflow['max_ns'])
        worker_tails = [r for r in rows if r['event'] == 'tail' and r['worker'] == worker]
        require(len(worker_tails) == integer(buffer['count']) == integer(overflow['count']), 'tail count mismatch')
        require(len(worker_tails) <= buffer['capacity'], 'capacity exhausted')
        seen = set()
        ranges = [r for r in rows if r['event'] == 'range' and r['worker'] == worker]
        expected, counts = 1, dict.fromkeys(('completed','rejected','unknown','not_sent'), 0)
        for r in ranges:
            require(integer(r['first'], 1) == expected, 'range gap or overlap')
            last = integer(r['last'], expected)
            require(r['outcome'] in counts, 'invalid outcome')
            counts[r['outcome']] += last - expected + 1
            expected = last + 1
        require(expected == integer(outcome['last']) + 1, 'truncated outcomes')
        require(all(integer(outcome[k]) == v for k, v in counts.items()), 'outcome count mismatch')
        require(outcome['accepted'] == count + counts['unknown'] + counts['not_sent'], 'accepted mismatch')
        for r in worker_tails:
            sequence = integer(r['sequence'], 1, outcome['last'])
            require(sequence not in seen, 'duplicate tail key')
            seen.add(sequence)
            require(any(x['outcome'] == 'completed' and x['first'] <= sequence <= x['last'] for x in ranges), 'tail not completed')
            tails.append(integer(r['elapsed_ns'], 100000000, overflow['max_ns']))
        buckets = [r for r in rows if r['event'] == 'bucket' and r['worker'] == worker]
        require(len({r['upper_us'] for r in buckets}) == len(buckets), 'duplicate bucket')
        finite = 0
        for r in buckets:
            upper = integer(r['upper_us'], 10, 100000)
            require(upper % 10 == 0, 'bucket width changed')
            n = integer(r['count'], 1)
            finite += n
            histogram[upper] = histogram.get(upper, 0) + n
        require(finite + len(worker_tails) == count, 'completion conservation failed')
        if worker_tails:
            require(max(r['elapsed_ns'] for r in worker_tails) == overflow['max_ns'], 'tail max mismatch')
    for r in rows:
        if r['event'] in ('tail','bucket','range'):
            require(r['worker'] in outcomes, 'unknown worker')
    tails.sort()
    quantiles = {}
    for name, fraction in (('p50', .5), ('p95', .95), ('p99', .99)):
        rank = math.ceil(completed * fraction)
        cumulative = 0
        value = None
        tail_rank = None
        for upper, count in sorted(histogram.items()):
            cumulative += count
            if rank <= cumulative:
                value = upper * 1000
                break
        if completed and value is None:
            tail_rank = rank - sum(histogram.values())
            value = tails[tail_rank - 1]
        quantiles[name] = {'rank': rank, 'tail_rank': tail_rank, 'ns': value}
    return {'version': VERSION, 'completed': completed, 'overflow_count': len(tails), 'quantiles': quantiles}


def audit_trial(directory, result):
    require(not (directory / 'stderr.log').read_text().strip(), 'native stderr is nonempty')
    rows = read_rows(directory / 'native.jsonl')
    identity = json.loads((directory / 'identity.json').read_text())
    token = identity['token']
    require(type(token) is str and len(token) == 64 and all(c in '0123456789abcdef' for c in token), 'invalid trial token')
    require([r for r in rows if r['event'] == 'measurement_identity'] == [{
        'event': 'measurement_identity', 'version': VERSION, 'token': token,
        'kind': result['settings']['kind'], 'trace_enabled': int(identity['trace_enabled']),
        'warmup': result['warmup_seconds'], 'sample': result['sample_seconds']}], 'raw trial identity mismatch')
    for phase in ('warmup', 'sample'):
        audited = audit_phase(rows, phase)
        if phase == 'sample':
            sample = audited
    require(sample == result['measurement'], 'raw measurement differs from result')
    require(sample['completed'] == result['completed'], 'completed mismatch')
    require(sample['overflow_count'] == result['latency_overflow_count'], 'overflow mismatch')
    starts = [r['ns'] for r in rows if r['event'] == 'phase_start' and r['phase'] == 'sample']
    ends = [r['ns'] for r in rows if r['event'] == 'phase_end' and r['phase'] == 'sample']
    require(len(starts) == len(ends) == 1 and ends[0] > starts[0], 'invalid phase interval')
    elapsed = (ends[0] - starts[0]) / 1e9
    require(result['actual_sample_seconds'] == elapsed, 'elapsed mismatch')
    require(result['throughput_per_s'] == sample['completed'] / elapsed, 'throughput mismatch')
    for name, quantile in sample['quantiles'].items():
        legacy = ({'lower_bound_us': 100000, 'upper_bound_us': None}
                  if quantile['tail_rank'] is not None else
                  quantile['ns'] // 1000 if quantile['ns'] is not None else None)
        require(result['latency_us'][name] == legacy, 'legacy histogram differs from raw evidence')
    raw_max = max(r['max_ns'] for r in rows if r['event'] == 'overflow' and r['phase'] == 'sample')
    require(result['latency_max_ns'] == raw_max, 'max differs from raw evidence')
    return sample


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_comparison(pairs):
    import statistics
    modes = ('off', 'on')
    require(len(pairs) == 3, 'three pairs required')
    throughput = {m: [p[m]['throughput_per_s'] for p in pairs] for m in modes}
    p99 = {m: [p[m]['measurement']['quantiles']['p99']['ns'] for p in pairs] for m in modes}
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for series in (throughput, p99) for values in series.values() for v in values), 'missing or invalid metrics')
    cv = {label: {m: statistics.pstdev(series[m]) / statistics.mean(series[m]) for m in modes} for label, series in (('throughput', throughput), ('p99', p99))}
    drop = 1 - statistics.median(throughput['on']) / statistics.median(throughput['off'])
    rise = statistics.median(p99['on']) / statistics.median(p99['off']) - 1
    # Compare medians directly, preserving the established inclusive boundaries.
    verdict = 'indeterminate' if any(v > .15 for group in cv.values() for v in group.values()) else 'investigate' if statistics.median(throughput['on']) < statistics.median(throughput['off']) * .8 or statistics.median(p99['on']) > statistics.median(p99['off']) * 1.25 else 'within_trigger'
    return {'version': VERSION, 'verdict': verdict, 'population_cv': cv, 'throughput': throughput, 'p99_ns': p99, 'throughput_drop_fraction': drop, 'p99_rise_fraction': rise}


def audit_trace(directory, report, source):
    require(report['source_commit'] == source['commit'], 'candidate commit mismatch')
    require(report['measurement_protocol'] == {'version': VERSION, 'build': 'debug-native', 'warmup': 5, 'sample': 30, 'pairs': 3, 'order': ['off','on']}, 'protocol mismatch')
    require(report['source_identity'] == source, 'candidate sources mismatch')
    binary_hash = file_hash(directory / 'benchmark_driver.exe')
    pairs = report['trace_ab']['pairs']
    require(len(pairs) == 3, 'missing pair')
    tokens = set()
    for i, pair in enumerate(pairs, 1):
        require(set(pair) == {'off','on'}, 'unexpected mode')
        for mode, result in pair.items():
            trial = directory / f'trace-ab-{i}-{mode}'
            require(json.loads((trial / 'result.json').read_text()) == result, 'summary differs from trial')
            identity = json.loads((trial / 'identity.json').read_text())
            require(identity == {'version': VERSION, 'binary_sha256': binary_hash, 'token': identity['token'], 'settings': result['settings'], 'warmup': 5, 'sample': 30, 'trace_enabled': mode == 'on', 'trial': trial.name}, 'trial identity mismatch')
            require(identity['token'] not in tokens, 'reused trial token')
            tokens.add(identity['token'])
            require(result['native']['sha256'] == binary_hash and result['native']['pid'] > 0, 'binary identity mismatch')
            require(result['settings'] == {'name':'durable-stages','kind':'durable'}, 'workload mismatch')
            require(result['warmup_seconds'] == 5 and result['sample_seconds'] == 30, 'budget mismatch')
            require(result['sqlite_trace_enabled'] == (mode == 'on'), 'trace mode mismatch')
            if mode == 'on':
                require((trial / 'sqlite.jsonl').stat().st_size > 0, 'trace output missing')
            else:
                require(not (trial / 'sqlite.jsonl').exists(), 'trace-off output present')
            audit_trial(trial, result)
    comparison = trace_comparison(pairs)
    require(comparison == report['trace_ab']['comparison'], 'stored verdict differs from raw evidence')
    return comparison
