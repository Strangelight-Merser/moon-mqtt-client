#!/usr/bin/env python3
"""Finite-window ESP logical receipt audit. Never a physical/exactly-once claim."""
import argparse,json
from pathlib import Path


def audit(rows, wire, expected_ids):
    unknown=lambda why:{'verdict':'UNKNOWN','reason':why,'physical_output':'NOT_PROVEN'}
    if not rows or rows[0].get('event')!='capture_open' or rows[-1].get('event')!='capture_closed':
        return unknown('capture is not bookended')
    statuses=[(i,r) for i,r in enumerate(rows) if r.get('event')=='status']
    if len(statuses)!=2:return unknown('requires exactly two fresh serial status bookends')
    (start,before),(end,after)=statuses
    if not before.get('boot_id') or before['boot_id']!=after.get('boot_id'):return unknown('boot changed or missing')
    if not isinstance(before.get('at'),(int,float)) or not isinstance(after.get('at'),(int,float)) or before['at']>=after['at']:return unknown('missing capture clock bookends')
    wire=[r for r in wire if before['at']<=r.get('at',-1)<=after['at']]
    inside=rows[start+1:end]
    if any(r.get('event') in ['moon_esp32_boot','restarting','capture_error'] or 'raw' in r for r in inside):
        return unknown('restart or incomplete serial record inside window')
    applied=[r for r in inside if r.get('event')=='applied']
    counts=[r.get('applied_count') for r in applied]
    first=before.get('applied_count');last=after.get('applied_count')
    if not isinstance(first,int) or not isinstance(last,int) or counts!=list(range(first+1,last+1)):
        return unknown('applied counter is not complete and consecutive')
    found=[]
    for ident in expected_ids:
        hits=[r for r in applied if r.get('id')==ident]
        commands=[r for r in wire if r.get('topic','').endswith('/device/set') and isinstance(r.get('body'),dict) and r['body'].get('id')==ident]
        if len(hits)!=1 or not commands or any(r.get('retained') for r in commands):
            return unknown('missing, duplicated or unbound receipt for '+ident)
        if any(r['body'].get('target')!=hits[0].get('state') for r in commands):return unknown('command/receipt state mismatch')
        found.append({'id':ident,'logical_apply_records':1,'applied_count':hits[0]['applied_count'],'state':hits[0]['state']})
    return {'verdict':'VERIFIED_LOGICAL_WINDOW','boot_id':before['boot_id'],'count_before':first,'count_after':last,'commands':found,'physical_output':'NOT_PROVEN','across_restart':'NOT_PROVEN','query_alone':'NOT_SUFFICIENT'}


def self_test():
    import copy
    rows=[{'event':'capture_open'},{'event':'status','boot_id':'one','applied_count':0,'at':1},{'event':'applied','id':'x','state':'ON','applied_count':1},{'event':'status','boot_id':'one','applied_count':1,'at':3},{'event':'capture_closed'}]
    wire=[{'topic':'test/device/set','body':{'id':'x','target':'ON'},'retained':False,'at':2}]
    assert audit(rows,wire,['x'])['verdict']=='VERIFIED_LOGICAL_WINDOW'
    mutations=[]
    r=copy.deepcopy(rows);r.pop(2);mutations.append(r)
    r=copy.deepcopy(rows);r[-2]['boot_id']='two';mutations.append(r)
    mutations.append(rows[:-1])
    r=copy.deepcopy(rows);r.insert(2,{'event':'capture_error'});mutations.append(r)
    r=copy.deepcopy(rows);r.insert(3,r[2]);mutations.append(r)
    for r in mutations:assert audit(r,wire,['x'])['verdict']=='UNKNOWN'
    assert audit(rows,[],['x'])['verdict']=='UNKNOWN'
    return {'valid':1,'negative_controls':6,'result':'PASS'}

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path);ap.add_argument('--self-test',action='store_true');args=ap.parse_args()
    if args.self_test:print(json.dumps(self_test()))
    else:
        read=lambda name:[json.loads(x) for x in (args.run/name).read_text().splitlines()]
        result=audit(read('serial.jsonl'),read('wire.jsonl'),json.loads((args.run/'commands.json').read_text()))
        (args.run/'receipt-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));raise SystemExit(0 if result['verdict']=='VERIFIED_LOGICAL_WINDOW' else 1)
