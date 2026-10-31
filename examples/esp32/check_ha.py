#!/usr/bin/env python3
"""Real HA + native MoonBit controller + USB-attached ESP32 acceptance.
Resets the board and restarts the named lab broker. Never writes a HA state.
"""
import argparse,json,pathlib,serial,subprocess,threading,time,urllib.request,uuid
import paho.mqtt.client as mqtt
ap=argparse.ArgumentParser();ap.add_argument('--broker',type=pathlib.Path,required=True)
ap.add_argument('--ha',type=pathlib.Path,required=True);ap.add_argument('--output',type=pathlib.Path,required=True)
ap.add_argument('--container',default='moon-esp32-hotspot');ap.add_argument('--port',default='/dev/cu.usbserial-110')
a=ap.parse_args();b=json.loads(a.broker.read_text());h=json.loads(a.ha.read_text())
a.output.mkdir(parents=True,exist_ok=False)
results=[];wire=[];serial_rows=[];ha_states=[];stop=threading.Event();subscribed=threading.Event()
logs={k:(a.output/(k+'.jsonl')).open('w') for k in ['wire','serial','ha']}
lock=threading.Lock()
def record(kind,row):
    with lock:logs[kind].write(json.dumps(row)+'\n');logs[kind].flush()
def http(path,data=None):
    req=urllib.request.Request(h['url']+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+h['access_token'],'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
def state():return http('/api/states/'+entity)['state']
def poll_ha():
    while not stop.is_set():
        try:
            row={'time':time.time(),'state':state()};ha_states.append(row);record('ha',row)
        except Exception as e:record('ha',{'time':time.time(),'error':type(e).__name__})
        stop.wait(.2)
def wait(predicate,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.05)
    raise AssertionError('Timed out waiting for live evidence')
def passed(case,**ev):
    row={'case':case,'result':'PASS',**ev};results.append(row);print(json.dumps(row),flush=True)
def smatch(event,start=0):return next((r for r in serial_rows[start:] if r.get('event')==event),None)
def wmatch(suffix,pred=lambda body:True,start=0):
    return next((r for r in wire[start:] if r['topic']==b['prefix']+suffix and pred(r['body'])),None)
def on_connect(c,u,f,rc,p):
    if rc==0:c.subscribe(b['prefix']+'/#',1)
def on_message(c,u,m):
    body=m.payload.decode(errors='replace')
    try:body=json.loads(body)
    except ValueError:pass
    row={'time':time.time(),'topic':m.topic,'body':body,'retained':bool(m.retain)};wire.append(row);record('wire',row)
client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='moon-ha-hil-'+uuid.uuid4().hex[:10])
client.username_pw_set(b['host_username'],b['host_password']);client.tls_set(ca_certs=b['ca_file'])
client.on_connect=on_connect;client.on_message=on_message;client.on_subscribe=lambda *args:subscribed.set()
s=serial.Serial(port=None,baudrate=115200,timeout=.2);s.dtr=False;s.rts=False;s.port=a.port
threads=[]
def read_serial():
    while not stop.is_set():
        line=s.readline().decode(errors='replace').strip()
        if not line:continue
        try:r=json.loads(line)
        except ValueError:r={'raw':line}
        serial_rows.append(r);record('serial',{'time':time.time(),**r})
def serial_command(op):s.write((json.dumps({'op':op})+'\n').encode());s.flush()
def status():
    n=len(serial_rows);serial_command('status');return wait(lambda:smatch('status',n),5)
def ha_set(target):return http('/api/services/switch/turn_'+target.lower(),{'entity_id':entity})
def pub(suffix,body,retain=False):
    info=client.publish(b['prefix']+suffix,json.dumps(body) if isinstance(body,dict) else body,qos=1,retain=retain);info.wait_for_publish(5)
try:
    candidates=[x['entity_id'] for x in http('/api/states') if x['entity_id'].startswith('switch.') and x.get('attributes',{}).get('friendly_name')=='Moon relay']
    assert len(candidates)==1,candidates
    entity=candidates[0]
    client.connect('127.0.0.1',b['port'],15);client.loop_start();assert subscribed.wait(10)
    s.open()
    for target in [read_serial,poll_ha]:
        t=threading.Thread(target=target,daemon=True);t.start();threads.append(t)
    start=time.monotonic();wait(lambda:smatch('mqtt_ready'),90)
    wait(lambda:state()=='off');st=status()
    assert st['clock_ready'] and st['mqtt_connected']
    passed('cold_boot_sntp_tls_without_usb_time',seconds=round(time.monotonic()-start,2),boot_id=st['boot_id'],entity=entity)
    for target in ['ON','OFF','ON','OFF']:
        n=len(wire);sn=len(serial_rows);start=time.monotonic();ha_set(target)
        cmd=wait(lambda:wmatch('/device/set',lambda d:isinstance(d,dict) and d.get('target')==target,n))['body']
        fb=wait(lambda:wmatch('/device/feedback',lambda d:isinstance(d,dict) and d.get('correlation_id')==cmd['id'] and d.get('state')==target,n))
        applied=wait(lambda:smatch('applied',sn));assert applied['id']==cmd['id']
        wait(lambda:state()==target.lower())
        passed('ha_service_to_real_esp_'+target,id=cmd['id'],seconds=round(time.monotonic()-start,3),applied_count=applied['applied_count'])
    old=status()['boot_id'];n=len(wire);hn=len(ha_states);start=time.monotonic()
    # Verified esptool UART reset polarity: DTR deasserted, RTS asserted holds EN low.
    s.dtr=False;s.rts=True
    ha_set('ON')
    cmd=wait(lambda:wmatch('/device/set',lambda d:isinstance(d,dict) and d.get('target')=='ON',n))['body']
    wait(lambda:wmatch('/device/availability',lambda d:d=='offline',n),45)
    wait(lambda:state()=='unavailable',15)
    assert not any(r['state']=='on' for r in ha_states[hn:])
    assert not wmatch('/device/feedback',lambda d:isinstance(d,dict) and d.get('correlation_id')==cmd['id'],n)
    passed('device_hard_offline_lwt_and_nonoptimistic_ha',seconds=round(time.monotonic()-start,2),unconfirmed_id=cmd['id'])
    # Queue a retained command while the real device is halted, then resubscribe.
    retained={'id':'retained-'+uuid.uuid4().hex,'target':'ON','expires_at_ms':int(time.time()*1000)+30000}
    pub('/device/set',retained,True);sn=len(serial_rows);n=len(wire);s.rts=False
    wait(lambda:smatch('mqtt_ready',sn),90)
    wait(lambda:smatch('retained_request_rejected',sn),15)
    pub('/device/set','',True)
    wait(lambda:state()=='off',20);st=status()
    assert st['boot_id']!=old and st['applied_count']==0
    passed('retained_command_rejected_after_hardware_reset',new_boot_id=st['boot_id'],applied_count=0)
    # Preserve real ON across broker outage, with no command replay or reboot.
    n=len(wire);ha_set('ON');wait(lambda:state()=='on')
    before=status();n=len(wire);start=time.monotonic()
    subprocess.run(['docker','stop','--time','3',a.container],check=True,stdout=subprocess.DEVNULL)
    wait(lambda:state()=='unavailable',20)
    subprocess.run(['docker','start',a.container],check=True,stdout=subprocess.DEVNULL)
    wait(lambda:state()=='on',90);after=status()
    assert after['boot_id']==before['boot_id'] and after['applied_count']==before['applied_count']
    assert not wmatch('/device/set',start=n)
    passed('broker_restart_recovers_ha_on_without_replay',seconds=round(time.monotonic()-start,2),applied_count=after['applied_count'])
    ha_set('OFF');wait(lambda:state()=='off');passed('final_ha_and_device_off',**status())
except Exception as e:
    results.append({'case':'acceptance','result':'FAIL','error':type(e).__name__,'reason':str(e)});raise
finally:
    if s.is_open:s.rts=False
    # Restore only this lab broker if a case failed while it was stopped.
    subprocess.run(['docker','start',a.container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if client.is_connected():
        try:pub('/device/set','',True)
        except Exception:pass
    stop.set()
    for t in threads:t.join(timeout=2)
    if s.is_open:s.close()
    client.disconnect();client.loop_stop()
    for f in logs.values():f.close()
    (a.output/'results.json').write_text(json.dumps(results,indent=2)+'\n')
