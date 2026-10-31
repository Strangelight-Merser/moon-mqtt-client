#!/usr/bin/env python3
"""Provision this device over USB without printing credentials."""
import argparse, json, pathlib, sys, time
import serial

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--port',default='/dev/cu.usbserial-110')
    ap.add_argument('--network',type=pathlib.Path)
    ap.add_argument('--auth-mode',choices=['peap','psk'],default='peap')
    ap.add_argument('--broker',type=pathlib.Path)
    ap.add_argument('--log',type=pathlib.Path,required=True)
    ap.add_argument('--timeout',type=int,default=75)
    ap.add_argument('--operation',choices=['configure','status','restart','time','retry_wifi','scan'],default='configure')
    args=ap.parse_args();redact=[]
    payload={'op':args.operation,'epoch_ms':int(time.time()*1000)}
    if args.operation=='configure':
        if not args.network or not args.broker:ap.error('configure requires --network and --broker')
        network=json.loads(args.network.read_text());broker=json.loads(args.broker.read_text())
        for k in (('ssid','username','password') if args.auth_mode=='peap' else ('ssid','password')):
            if not isinstance(network.get(k),str) or not network[k]:raise SystemExit('Missing network field: '+k)
        redact=[network.get('username',''),network['password'],broker['device_password'],broker['host_password']]
        payload['config']={'auth_mode':args.auth_mode,'ssid':network['ssid'],'username':network.get('username','') if args.auth_mode=='peap' else '', 'password':network['password'],
          'broker_host':broker['host'],'broker_port':broker['port'],'broker_user':broker['device_username'],
          'broker_password':broker['device_password'],'broker_ca':pathlib.Path(broker['ca_file']).read_text(),'prefix':broker['prefix']}
    s=serial.Serial(port=None,baudrate=115200,timeout=.2,write_timeout=5)
    s.dtr=False;s.rts=False;s.port=args.port;s.open()
    args.log.parent.mkdir(parents=True,exist_ok=True)
    ready=False;scan_failed=False
    try:
        time.sleep(.5);s.reset_input_buffer()
        s.write(json.dumps(payload,ensure_ascii=False).encode()+b'\n');s.flush()
        end=time.monotonic()+args.timeout
        with args.log.open('w') as log:
            while time.monotonic()<end:
                raw=s.readline()
                if not raw:continue
                line=raw.decode(errors='replace').rstrip()
                for secret in redact:
                    if secret:line=line.replace(secret,'[REDACTED]')
                print(line,flush=True);log.write(line+'\n');log.flush()
                try:d=json.loads(line)
                except ValueError:continue
                if d.get('event')=='scan_result' and d.get('count',-1)<0:scan_failed=True
                if d.get('event')=='scan_done' and args.operation=='scan':ready=not scan_failed;break
                if d.get('event')=='mqtt_ready':ready=True;break
                if d.get('event')=='status' and args.operation=='status':ready=True;break
                if d.get('event')=='clock_set_from_local_host' and args.operation=='time':ready=True;break
                if d.get('event') in ('invalid_config','config_save_failed','wifi_timeout_manual_retry_required'):break
    finally:s.close()
    return 0 if ready else 2
if __name__=='__main__':sys.exit(main())
