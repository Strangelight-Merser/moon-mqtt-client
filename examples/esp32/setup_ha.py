#!/usr/bin/env python3
"""Onboard an explicitly authorized new local HA lab using its HTTP config flows.
Secrets are read/written only in the supplied private credentials file.
"""
import argparse,json,pathlib,urllib.request,urllib.parse,uuid
ap=argparse.ArgumentParser();ap.add_argument('--credentials',type=pathlib.Path,required=True)
a=ap.parse_args();c=json.loads(a.credentials.read_text());base=c['url'];client_id=base+'/'
def save():a.credentials.write_text(json.dumps(c,indent=2)+'\n');a.credentials.chmod(0o600)
def request(path,data=None,*,raw=None,content_type=None,auth=True):
    headers={}
    if auth and c.get('access_token'):headers['Authorization']='Bearer '+c['access_token']
    if data is not None:raw=json.dumps(data).encode();content_type='application/json'
    if content_type:headers['Content-Type']=content_type
    req=urllib.request.Request(base+path,data=raw,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
    except urllib.error.HTTPError as e:
        # Do not print server bodies, which may reflect secrets.
        raise RuntimeError(f'HA endpoint {path} returned HTTP {e.code}') from None
status=request('/api/onboarding',auth=False)
if not any(x['step']=='user' and x['done'] for x in status):
    result=request('/api/onboarding/users',{'name':'ESP32 Lab','username':c['username'],'password':c['password'],'client_id':client_id,'language':'en'},auth=False)
    token=request('/auth/token',raw=urllib.parse.urlencode({'grant_type':'authorization_code','code':result['auth_code'],'client_id':client_id}).encode(),content_type='application/x-www-form-urlencoded',auth=False)
    c.update({k:token[k] for k in ('access_token','refresh_token')});save();print('Created local lab owner')
else:
    if not c.get('refresh_token'):raise SystemExit('Existing HA owner found; refusing to replace it.')
    token=request('/auth/token',raw=urllib.parse.urlencode({'grant_type':'refresh_token','refresh_token':c['refresh_token'],'client_id':client_id}).encode(),content_type='application/x-www-form-urlencoded',auth=False)
    c['access_token']=token['access_token'];save()
for step in status:
    if not step['done'] and step['step'] in ('core_config','analytics','integration'):
        body={'client_id':client_id,'redirect_uri':base+'/?auth_callback=1'} if step['step']=='integration' else {}
        request('/api/onboarding/'+step['step'],body);print('Completed onboarding:',step['step'])
entries=request('/api/config/config_entries/entry')
if any(x['domain']=='mqtt' for x in entries):print('MQTT entry already exists; keeping it');raise SystemExit(0)
flow=request('/api/config/config_entries/flow',{'handler':'mqtt','show_advanced_options':True})
if flow.get('step_id')!='broker':raise RuntimeError('Unexpected MQTT initial step: '+str(flow.get('step_id')))
boundary='moon-'+uuid.uuid4().hex
raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="broker-ca.pem"\r\nContent-Type: application/x-pem-file\r\n\r\n'.encode()+pathlib.Path(c['ca_file']).read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
upload=request('/api/file_upload',raw=raw,content_type='multipart/form-data; boundary='+boundary)
result=request('/api/config/config_entries/flow/'+flow['flow_id'],{
 'broker':c['broker_host'],'port':c['broker_port'],'username':c['broker_username'],'password':c['broker_password'],'protocol':'5',
 'other_settings':{'client_id':'moon-home-assistant','keepalive':15,'set_ca_cert':'custom','certificate':upload['file_id'],'set_client_cert':False,'tls_insecure':False,'transport':'tcp'}})
if result.get('type')!='create_entry':
    print('MQTT setup incomplete:',result.get('type'),result.get('step_id'),result.get('errors'));raise SystemExit(2)
c['mqtt_entry_id']=result['result']['entry_id'];save();print('MQTT integration connected with custom CA and hostname verification')
