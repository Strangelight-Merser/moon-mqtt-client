#!/usr/bin/env python3
"""Run the existing MoonBit controller or CLI against the private TLS broker."""
import argparse,json,os,pathlib,subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
BUILD=ROOT/'_build/native/debug/build/Strangelight-Merser/moon-mqtt-client/examples'
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--broker',type=pathlib.Path,required=True)
 ap.add_argument('mode',choices=['controller','publish','subscribe']);ap.add_argument('arguments',nargs=argparse.REMAINDER)
 a=ap.parse_args();cfg=json.loads(a.broker.read_text());env=os.environ.copy()
 env.update(MQTT_DEMO_HOST='127.0.0.1',MQTT_DEMO_PORT=str(cfg['port']),MQTT_DEMO_TLS='1',MQTT_DEMO_CA_FILE=cfg['ca_file'],MQTT_DEMO_USERNAME=cfg['host_username'],MQTT_DEMO_PASSWORD=cfg['host_password'],HA_RELAY_PREFIX=cfg['prefix'],HA_RELAY_ENTITY_ID='moon_esp32',HA_RELAY_CONTROLLER_CLIENT_ID='moon-esp32-controller')
 if a.mode=='controller':cmd=[str(BUILD/'ha_relay/controller/controller.exe')]
 else:cmd=[str(BUILD/'mqtt_demo/cli/cli.exe'),a.mode,'--host','127.0.0.1','--port',str(cfg['port']),'--tls','--ca',cfg['ca_file']]+a.arguments
 if not pathlib.Path(cmd[0]).exists():raise SystemExit('Build the MoonBit native controller and CLI first.')
 return subprocess.call(cmd,env=env)
if __name__=='__main__':raise SystemExit(main())
