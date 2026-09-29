#!/usr/bin/env python3
"""Deterministic WSS boundary probes in generated diagnostic copies only."""
import argparse,hashlib,io,json,os,socket,ssl,subprocess,sys,tarfile,threading,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'tests'))
from build_paths import demo_binary
from harness import integration_fixture
from ws_protocol_faults import Peer,frame,exact
BASE='5ee6912fdfc8b9a9fd7747a316f1138d41000aca'

def replace(path,old,new):
 s=path.read_text();assert s.count(old)==1,(path,old,s.count(old));path.write_text(s.replace(old,new))
def prepare(out):
 source=out/'source';source.mkdir()
 with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git','archive',BASE],cwd=ROOT))) as t:t.extractall(source,filter='data')
 replace(source/'moon.pkg','  "moonbitlang/async/io",','  "moonbitlang/async/io",\n  "moonbitlang/async/stdio",')
 # All barriers block until reader-side close cancels the held operation.
 pause='@stdio.stdout.write(b"CLOSURE_BARRIER\\n"); ignore(@stdio.stdin.read_exactly(1))'
 replace(source/'session.mbt','      mark_started(request)','''      if request.kind is Disconnect && @env.get_env_var("CLOSURE_BOUNDARY") == Some("pre_entry") { '''+pause+''' }
      mark_started(request)''')
 replace(source/'session.mbt','        request.local_write_complete = true','''        request.local_write_complete = true
        if request.kind is Disconnect && @env.get_env_var("CLOSURE_BOUNDARY") == Some("complete") {
          if @env.get_env_var("CLOSURE_MUTATION") == Some("1") { request.local_write_complete = false }
          '''+pause+'''
        }''')
 replace(source/'session.mbt','    self.alive = false','    println("CLOSURE_FIRST_ABORT");\n    self.alive = false')
 replace(source/'session.mbt','    self.result = Some(result)','    if self.kind is Disconnect { println("CLOSURE_SETTLED") };\n    self.result = Some(result)')
 replace(source/'websocket_transport.mbt','  self.writer.write_once(buf, offset~, len~)','''  if @env.get_env_var("CLOSURE_BOUNDARY") == Some("partial") && len == 8 && buf[offset] == b'\\x82' && buf[offset + 1] == b'\\x82' {
    self.writer.write(buf[offset:offset + 4])
    '''+pause+'''
  }
  self.writer.write_once(buf, offset~, len~)''')
 replace(source/'websocket_transport.mbt','  len\n}','''  if @env.get_env_var("CLOSURE_BOUNDARY") == Some("held_writer") && len == 2 && bytes[0] == b'\\xe0' {
    '''+pause+'''
  }
  len
}''')
 (out/'source-hashes.json').write_text(json.dumps({str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source/'session.mbt',source/'websocket_transport.mbt',source/'moon.pkg']},indent=2))
 with (out/'build.log').open('w') as log:subprocess.run([str(source/'scripts/moon.sh'),'build','--target','native'],cwd=source,stdout=log,stderr=subprocess.STDOUT,check=True)
 return source,demo_binary(source,'cli')

def trial(source,cli,ctx,pki,boundary,mode,number,mutation):
 row=dict(boundary=boundary,close_mode=mode,repeat=number,mutation=mutation,events=[],peer_error=None)
 barrier=threading.Event();lines=[]
 with socket.socket() as listener:
  listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(10)
  def server():
   try:
    raw,_=listener.accept()
    with raw,ctx.wrap_socket(raw,server_side=True) as conn:
     conn.settimeout(6);peer=Peer(conn);peer.upgrade();assert peer.packet()[0]==16
     conn.sendall(frame(b'\x20\x02\x00\x00'));sub=peer.packet();assert sub[0]==130
     conn.sendall(frame(b'\x90\x03'+sub[2:4]+b'\x01'))
     topic=b'it/close';body=len(topic).to_bytes(2,'big')+topic+b'\x00\x01x'
     conn.sendall(frame(bytes([50,len(body)])+body));assert peer.packet()==b'\x40\x02\x00\x01'
     if boundary=='partial':
      row['partial_wire_hex']=exact(conn,4).hex()
     elif boundary!='pre_entry':
      row['disconnect_hex']=peer.packet().hex();assert row['disconnect_hex']=='e000'
     assert barrier.wait(4),'missing boundary marker'
     row['events'].append({'event':'peer_close_begin','at_ns':time.monotonic_ns()})
     if mode=='ws_close':conn.sendall(frame(b'\x03\xe8',opcode=8))
     elif mode=='tls_close_notify':
      try:conn.unwrap()
      except (ssl.SSLError,OSError):pass
     elif mode!='transport_eof':raise AssertionError(mode)
   except Exception as e:row['peer_error']=repr(e)
  worker=threading.Thread(target=server,daemon=True);worker.start()
  command=[str(cli),'subscribe','--host','localhost','--port',str(listener.getsockname()[1]),'--ws-path','/mqtt','--tls','--ca',str(pki/'ca.pem'),'--cert',str(pki/'client.pem'),'--key',str(pki/'client.key'),'--topic','it/close','--qos','1','--count','1','--timeout-ms','3000']
  env=os.environ.copy();env['CLOSURE_BOUNDARY']=boundary;env['CLOSURE_MUTATION']='1' if mutation else '0'
  proc=subprocess.Popen(command,cwd=source,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
  def reader():
   for line in proc.stdout:
    lines.append(line)
    if line.startswith('CLOSURE_'):
     row['events'].append({'event':line.strip(),'at_ns':time.monotonic_ns()})
     if line.startswith('CLOSURE_BARRIER'):barrier.set()
  drain=threading.Thread(target=reader,daemon=True);drain.start()
  try:code=proc.wait(12)
  except subprocess.TimeoutExpired:proc.kill();code=proc.wait();row['timeout']=True
  drain.join(3);worker.join(8)
  row.update(returncode=code,stdout=''.join(lines),stderr=proc.stderr.read(),peer_joined=not worker.is_alive(),stdout_joined=not drain.is_alive())
  proc.stdin.close();proc.stdout.close();proc.stderr.close()
 expected=0 if boundary=='complete' and not mutation else 1
 row['pass']=code==expected and not row['peer_error'] and row['peer_joined'] and row['stdout_joined'] and not row.get('timeout') and any(x['event']=='CLOSURE_SETTLED' for x in row['events'])
 if expected:row['pass']=row['pass'] and ('NotSent' if boundary=='pre_entry' else 'OutcomeUnknown') in row['stderr']
 return row

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);args=ap.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False,parents=True)
 source,cli=prepare(out);pki=out/'pki';pki.mkdir();integration_fixture().write_pki(pki,mtls=True)
 ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(pki/'server.pem',pki/'server.key');ctx.load_verify_locations(pki/'client-ca.pem');ctx.verify_mode=ssl.CERT_REQUIRED
 report={'diagnostic_only':True,'base_commit':BASE,'binary_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'trials':[]}
 for boundary,mutation in [('pre_entry',False),('partial',False),('held_writer',False),('complete',False),('complete',True)]:
  for mode in ['ws_close','tls_close_notify','transport_eof']:
   for n in range(1,4):
    row=trial(source,cli,ctx,pki,boundary,mode,n,mutation);report['trials'].append(row)
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(boundary,mutation,mode,n,row['pass'],flush=True)
    assert row['pass'],row
 report['status']='PASS';(out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
