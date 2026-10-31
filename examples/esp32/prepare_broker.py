#!/usr/bin/env python3
"""Prepare a local authenticated TLS broker; never prints its passwords."""
import argparse, ipaddress, json, os, pathlib, secrets, subprocess

def run(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=pathlib.Path,required=True)
    ap.add_argument('--host',required=True);ap.add_argument('--port',type=int,default=8884)
    args=ap.parse_args();ipaddress.ip_address(args.host)
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True);os.chmod(root,0o700)
    if (root/'broker.json').exists():raise SystemExit('Existing broker settings: reuse or choose another output directory.')
    os.umask(0o077)
    run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-sha256','-days','365','-subj','/CN=Moon ESP32 Local CA','-keyout',str(root/'ca.key'),'-out',str(root/'ca.pem')])
    run(['openssl','req','-newkey','rsa:2048','-nodes','-sha256','-subj','/CN=moon-esp32-local','-keyout',str(root/'server.key'),'-out',str(root/'server.csr')])
    (root/'server.ext').write_text('subjectAltName=IP:'+args.host+',IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n')
    run(['openssl','x509','-req','-in',str(root/'server.csr'),'-CA',str(root/'ca.pem'),'-CAkey',str(root/'ca.key'),'-CAcreateserial','-days','30','-sha256','-extfile',str(root/'server.ext'),'-out',str(root/'server.pem')])
    cfg={'host':args.host,'port':args.port,'prefix':'moon/esp32/s3','device_username':'moon-esp32','device_password':secrets.token_urlsafe(24),'host_username':'moon-host','host_password':secrets.token_urlsafe(24),'ca_file':str(root/'ca.pem')}
    for i,role in enumerate(('device','host')):
        cmd=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}','-v',str(root)+':/config','eclipse-mosquitto:2','mosquitto_passwd','-b']
        if i==0:cmd+=['-c']
        cmd+=['/config/passwords',cfg[role+'_username'],cfg[role+'_password']]
        run(cmd)
    (root/'acl').write_text('user moon-esp32\ntopic read moon/esp32/s3/device/set\ntopic read moon/esp32/s3/device/query\ntopic write moon/esp32/s3/device/feedback\ntopic write moon/esp32/s3/device/availability\ntopic write moon/esp32/s3/device/telemetry\n\nuser moon-host\ntopic readwrite moon/esp32/s3/#\ntopic readwrite homeassistant/switch/moon_esp32/config\ntopic readwrite homeassistant/status\n')
    (root/'mosquitto.conf').write_text('listener 8883\nallow_anonymous false\npassword_file /config/passwords\nacl_file /config/acl\ncertfile /config/server.pem\nkeyfile /config/server.key\npersistence false\n')
    (root/'broker.json').write_text(json.dumps(cfg,indent=2))
    print('Broker files prepared:',root)
if __name__=='__main__':main()
