#!/usr/bin/env python3
"""Compile a fresh, separate consumer module and exchange data via Mosquitto."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import re

parser = argparse.ArgumentParser()
parser.add_argument('--registry', action='store_true', help='install the published package instead of a local workspace copy')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
module_text = (ROOT / 'moon.mod').read_text()
match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', module_text, re.MULTILINE)
if not match:
    raise RuntimeError('moon.mod does not contain a parseable version')
TARGET_VERSION = match.group(1)
spec = importlib.util.spec_from_file_location('mqtt_integration', ROOT / 'tests/integration/run.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
broker = h.Broker()
broker.start()
failed = True
try:
    with tempfile.TemporaryDirectory(prefix='moon-mqtt-consumer-') as temp:
        work = Path(temp)
        if not args.registry:
            library = work / 'library'
            library.mkdir()
            for name in ['moon.mod', 'moon.pkg', 'types.mbt', 'client.mbt', 'runtime.mbt',
                         'session.mbt', 'wire.mbt', 'LICENSE', 'NOTICE', 'README.md']:
                shutil.copy2(ROOT / name, library / name)
        consumer = work / 'consumer'
        consumer.mkdir()
        (consumer / 'moon.mod').write_text(f'''name = "acceptance/consumer"
preferred_target = "native"
import {{
  "Strangelight-Merser/moon-mqtt-client@{TARGET_VERSION}",
  "moonbitlang/async@0.21.3",
}}
''')
        (consumer / 'moon.pkg').write_text('''import {
  "Strangelight-Merser/moon-mqtt-client" @mqtt,
  "moonbitlang/async",
}
supported_targets = "+native"
pkgtype(kind: "executable")
''')
        (consumer / 'main.mbt').write_text('''async fn main {
  @mqtt.with_client(@mqtt.Config::new("127.0.0.1", "separate-consumer", port=PORT), async fn(client) {
    let result = client.subscribe([{ topic: "consumer/echo", qos: @mqtt.AtLeastOnce }])
    assert_true(result == [@mqtt.Granted(@mqtt.AtLeastOnce)])
    client.publish("consumer/echo", b"separate-module", qos=@mqtt.AtLeastOnce)
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) => {
          assert_true(message.payload == b"separate-module")
          break
        }
        @mqtt.Connected(_) => ()
        _ => fail("unexpected disconnect")
      }
    }
  })
  println("separate module consumer passed")
}
'''.replace('PORT', str(broker.port)))
        if args.registry:
            subprocess.run([str(h.MOON), 'update'], cwd=consumer, check=True)
        else:
            subprocess.run([str(h.MOON), 'work', 'init', 'library', 'consumer'], cwd=work, check=True)
        subprocess.run([str(h.MOON), 'run', 'consumer'], cwd=work, check=True, timeout=90)
        print('consumer source: Mooncakes registry' if args.registry else 'consumer source: local workspace')
        print(f'consumer target: Strangelight-Merser/moon-mqtt-client@{TARGET_VERSION}')
        failed = False
finally:
    broker.close(failed=failed)
