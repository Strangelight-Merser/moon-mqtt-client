#!/usr/bin/env python3
"""Bounded real-board acceptance. Requires provisioned board, broker, pyserial and paho-mqtt."""
import argparse, json, pathlib, subprocess, sys, threading, time, uuid
import serial
import paho.mqtt.client as mqtt

ap=argparse.ArgumentParser()
ap.add_argument('--broker',type=pathlib.Path,required=True)
ap.add_argument('--output',type=pathlib.Path,required=True)
ap.add_argument('--port',default='/dev/cu.usbserial-110')
a=ap.parse_args(); cfg=json.loads(a.broker.read_text()); a.output.mkdir(parents=True,exist_ok=True)
rows=[]; serial_rows=[]; results=[]; processes=[]; files=[]; stop=threading.Event()
wire_log=(a.output/'wire.jsonl').open('w'); serial_log=(a.output/'serial.log').open('w')
s=serial.Serial(port=None,baudrate=115200,timeout=.2);s.dtr=False;s.rts=False;s.port=a.port
client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='moon-hil-'+uuid.uuid4().hex[:12])
client.username_pw_set(cfg['host_username'],cfg['host_password']);client.tls_set(ca_certs=cfg['ca_file'])
def on_connect(c,u,f,rc,p):
    if rc==0:c.subscribe(cfg['prefix']+'/#',1)
def on_message(c,u,m):
    body=m.payload.decode(errors='replace')
    try: body=json.loads(body)
    except ValueError:pass
    row={'time':time.time(),'topic':m.topic,'body':body,'retained':bool(m.retain)}
    rows.append(row);wire_log.write(json.dumps(row)+'\n');wire_log.flush()
client.on_connect=on_connect;client.on_message=on_message

def read_serial():
    while not stop.is_set():
        line=s.readline().decode(errors='replace').strip()
        if not line:continue
        serial_log.write(line+'\n');serial_log.flush()
        try:serial_rows.append(json.loads(line))
        except ValueError:pass

def command(op):
    s.write((json.dumps({'op':op,'epoch_ms':int(time.time()*1000)})+'\n').encode());s.flush()
def wait(predicate,timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for live evidence')
def message(suffix,predicate=lambda b:True,start=0):
    return next((r for r in rows[start:] if r['topic']==cfg['prefix']+suffix and predicate(r['body'])),None)
def serial_event(name,start=0):return next((r for r in serial_rows[start:] if r.get('event')==name),None)
def status():
    n=len(serial_rows);command('status');return wait(lambda:serial_event('status',n),5)
def publish(suffix,body):
    info=client.publish(cfg['prefix']+suffix,json.dumps(body) if isinstance(body,dict) else body,qos=1)
    info.wait_for_publish(5)
def passed(name,**evidence):
    results.append({'case':name,'result':'PASS',**evidence});print(json.dumps(results[-1]),flush=True)
def controller(index):
    log=(a.output/f'controller-{index}.log').open('w');files.append(log)
    p=subprocess.Popen([sys.executable,str(pathlib.Path(__file__).with_name('host.py')),'--broker',str(a.broker),'controller'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    processes.append(p);return p

def stop_controller(p):
    import os,signal
    os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=5)
try:
    client.connect('127.0.0.1',cfg['port'],15);client.loop_start()
    s.open();thread=threading.Thread(target=read_serial,daemon=True);thread.start()
    time.sleep(1);command('time')
    wait(lambda:serial_event('mqtt_ready'),60)
    st=status();assert st['wifi_connected'] and st['mqtt_connected'] and st['clock_ready']
    passed('hotspot_authenticated_tls_mqtt',ip=st['ip'],boot_id=st['boot_id'])
    n=len(rows);p=controller(1)
    q=wait(lambda:message('/device/query',start=n))['body']['id']
    wait(lambda:message('/device/feedback',lambda b:isinstance(b,dict) and b.get('correlation_id')==q and b.get('state')=='OFF',n))
    wait(lambda:message('/ha/state',lambda b:b=='OFF',n));passed('native_controller_initial_query',id=q)
    for target in ['ON','OFF']*5+['ON']:
        n=len(rows);sn=len(serial_rows)
        r=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('host.py')),'--broker',str(a.broker),'publish','-t',cfg['prefix']+'/ha/set','-m',target,'--qos','1'],capture_output=True,text=True,timeout=15)
        assert r.returncode==0,r.stderr
        sent=wait(lambda:message('/device/set',lambda b:isinstance(b,dict) and b.get('target')==target,n))['body']
        feedback=wait(lambda:message('/device/feedback',lambda b:isinstance(b,dict) and b.get('correlation_id')==sent['id'] and b.get('state')==target,n))['body']
        wait(lambda:message('/ha/state',lambda b:b==target,n))
        applied=wait(lambda:serial_event('applied',sn));assert applied['id']==sent['id']
        expected_report={'event':'reported','id':sent['id'],'state':target}
        wait(lambda:any(json.loads(line)==expected_report for line in (a.output/'controller-1.log').read_text().splitlines() if line.startswith('{') and line.endswith('}')))
        passed('native_cli_controller_device_'+target,id=sent['id'],sequence=feedback['sequence'],applied_count=applied['applied_count'])
    before=status()['applied_count'];n=len(rows);stop_controller(p);p=controller(2)
    q=wait(lambda:message('/device/query',start=n))['body']['id']
    wait(lambda:message('/device/feedback',lambda b:isinstance(b,dict) and b.get('correlation_id')==q and b.get('state')=='ON',n))
    wait(lambda:message('/ha/state',lambda b:b=='ON',n));assert status()['applied_count']==before
    assert not message('/device/set',start=n)
    passed('controller_restart_preserves_device_state',applied_count=before)
    # Exercise device rejection/dedup separately, using the independent MQTT client.
    cmd={'id':'hil-'+uuid.uuid4().hex,'target':'ON','expires_at_ms':int(time.time()*1000)+25000}
    sn=len(serial_rows);publish('/device/set',cmd);wait(lambda:serial_event('applied',sn))
    before=status()['applied_count'];sn=len(serial_rows);publish('/device/set',cmd)
    wait(lambda:serial_event('duplicate_command',sn));assert status()['applied_count']==before
    passed('duplicate_no_reapplication',applied_count=before)
    sn=len(serial_rows);publish('/device/set',{**cmd,'target':'OFF'})
    wait(lambda:serial_event('conflicting_command_id',sn));assert status()['applied_count']==before
    passed('conflicting_id_rejected')
    sn=len(serial_rows);publish('/device/set',{'id':'expired-'+uuid.uuid4().hex,'target':'OFF','expires_at_ms':int(time.time()*1000)-1000})
    wait(lambda:serial_event('expired_or_invalid_command',sn));assert status()['applied_count']==before
    passed('expired_command_rejected')
    old=status()['boot_id'];sn=len(serial_rows);n=len(rows);command('restart')
    wait(lambda:serial_event('moon_esp32_boot',sn));command('time')
    wait(lambda:serial_event('mqtt_ready',sn),60)
    wait(lambda:message('/ha/state',lambda b:b=='OFF',n),20)
    st=status();assert st['boot_id']!=old and st['applied_count']==0
    passed('device_restart_fresh_identity_and_query',boot_id=st['boot_id'],clock_source='USB host time supplied')
except Exception as e:
    results.append({'case':'acceptance','result':'FAIL','reason':str(e)});raise
finally:
    for p in processes:
        if p.poll() is None:stop_controller(p)
    stop.set()
    if s.is_open:
        if 'thread' in globals():thread.join(timeout=1)
        s.close()
    client.disconnect();client.loop_stop()
    wire_log.close();serial_log.close()
    for f in files:f.close()
    (a.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
