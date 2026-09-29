#!/usr/bin/env python3
"""Ten unmodified native subscriber-close paths against pinned EMQX."""
import argparse,hashlib,json,queue,subprocess,sys,threading,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'))
from build_paths import demo_binary
from ws_broker import WsBroker
import paho.mqtt.client as paho

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 cli=demo_binary(ROOT,'cli');b=WsBroker();rows=[]
 report={'binary_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'trials':rows}
 try:
  b.start();oracle=paho.Client(paho.CallbackAPIVersion.VERSION2);oracle.connect('127.0.0.1',b.tcp_port);oracle.loop_start()
  for n in range(10):
   topic='it/closure/'+uuid.uuid4().hex
   cmd=[str(cli),'subscribe','--host','localhost','--port',str(b.wss_port),'--ws-path','/mqtt','--tls','--ca',str(b.temp/'ca.pem'),'--cert',str(b.temp/'client.pem'),'--key',str(b.temp/'client.key'),'--topic',topic,'--qos','1','--count','1','--timeout-ms','5000']
   p=subprocess.Popen(cmd,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE);q=queue.Queue();lines=[]
   def reader():
    for line in p.stdout:lines.append(line);q.put(line)
   t=threading.Thread(target=reader);t.start()
   try:
    first=json.loads(q.get(timeout=10));assert first['event']=='subscribed',first
    oracle.publish(topic,'closure-'+str(n),qos=1).wait_for_publish(5)
    code=p.wait(12);t.join(3);err=p.stderr.read();events=[json.loads(x) for x in lines]
    ok=code==0 and any(e.get('event')=='message' and e.get('payload')=='closure-'+str(n) for e in events)
    rows.append({'trial':n+1,'returncode':code,'events':events,'stderr':err,'pass':ok})
    (a.output/'summary.json').write_text(json.dumps(report,indent=2));assert ok,rows[-1]
   finally:
    if p.poll() is None:p.kill();p.wait()
    t.join(3);p.stdout.close();p.stderr.close()
  oracle.disconnect();oracle.loop_stop();report['status']='PASS'
 finally:
  b.close();(a.output/'summary.json').write_text(json.dumps(report,indent=2))
if __name__=='__main__':main()
