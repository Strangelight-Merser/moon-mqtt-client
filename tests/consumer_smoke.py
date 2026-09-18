#!/usr/bin/env python3
"""Compile a fresh, separate consumer module and exchange data via Mosquitto."""
import argparse
import importlib.util
import os
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


def local_async_tls() -> Path:
    for path in (ROOT / 'third_party' / 'async-tls', ROOT.parent / 'moon-async-tls'):
        if path.is_dir():
            return path
    raise RuntimeError('local async-tls module not found')


def copy_local_workspace(work: Path) -> None:
    library = work / 'library'
    library.mkdir()
    for name in ['moon.mod', 'moon.pkg', 'types.mbt', 'client.mbt', 'runtime.mbt',
                 'session.mbt', 'wire.mbt', 'LICENSE', 'NOTICE', 'README.md']:
        shutil.copy2(ROOT / name, library / name)
    async_tls = local_async_tls()
    shutil.copytree(
        async_tls, work / 'async-tls',
        ignore=shutil.ignore_patterns('_build', '.mooncakes', '.git', '.trash', '.DS_Store'),
        ignore_dangling_symlinks=True,
        symlinks=False,
    )


def write_consumer(consumer: Path, main: str) -> None:
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
    (consumer / 'main.mbt').write_text(main)


def run_consumer(work: Path, consumer: Path) -> None:
    env = os.environ.copy()
    if args.registry:
        env.pop('MOON_WORK', None)
        subprocess.run([str(h.MOON), 'update'], cwd=consumer, check=True, env=env)
        # `moon run` requires an explicit package or file entry; the consumer is
        # the module root here, so `.` selects it instead of relying on a
        # default that the toolchain does not provide.
        subprocess.run([str(h.MOON), 'run', '.'], cwd=consumer, check=True, timeout=90, env=env)
    else:
        subprocess.run(
            [str(h.MOON), 'work', 'init', 'library', 'async-tls', 'consumer'],
            cwd=work, check=True, env=env,
        )
        subprocess.run([str(h.MOON), 'run', 'consumer'], cwd=work, check=True, timeout=90, env=env)


broker = h.Broker()
broker.start()
failed = True
try:
    with tempfile.TemporaryDirectory(prefix='moon-mqtt-consumer-') as temp:
        work = Path(temp)
        if not args.registry:
            copy_local_workspace(work)
        write_consumer(work / 'consumer', '''async fn main {
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
        run_consumer(work, work / 'consumer')
        print('consumer source: Mooncakes registry' if args.registry else 'consumer source: local workspace')
        print(f'consumer target: Strangelight-Merser/moon-mqtt-client@{TARGET_VERSION}')
        failed = False
finally:
    broker.close(failed=failed)

mtls = h.Broker(mtls=True)
mtls.start()
mtls_failed = True
try:
    with tempfile.TemporaryDirectory(prefix='moon-mqtt-consumer-mtls-') as temp:
        work = Path(temp)
        if not args.registry:
            copy_local_workspace(work)
        write_consumer(work / 'consumer', '''async fn main {
  @mqtt.with_client(@mqtt.Config::new(
    "localhost",
    "separate-consumer-mtls",
    port=__PORT__,
    tls=@mqtt.CustomCA("__CA_FILE__"),
    client_identity=Some({
      certificate_chain_file: "__CERT_FILE__",
      private_key_file: "__KEY_FILE__",
    }),
  ), async fn(client) {
    let result = client.subscribe([{ topic: "consumer/mtls", qos: @mqtt.AtLeastOnce }])
    assert_true(result == [@mqtt.Granted(@mqtt.AtLeastOnce)])
    client.publish("consumer/mtls", b"separate-mtls", qos=@mqtt.AtLeastOnce)
    while true {
      match client.next_event() {
        @mqtt.MessageReceived(message) => {
          assert_true(message.payload == b"separate-mtls")
          break
        }
        @mqtt.Connected(_) => ()
        _ => fail("unexpected disconnect")
      }
    }
  })
  println("separate module mTLS consumer passed")
}
'''.replace('__PORT__', str(mtls.port))
  .replace('__CA_FILE__', str(mtls.temp / 'ca.pem'))
  .replace('__CERT_FILE__', str(mtls.temp / 'client.pem'))
  .replace('__KEY_FILE__', str(mtls.temp / 'client.key')))
        run_consumer(work, work / 'consumer')
        print('mTLS consumer source: Mooncakes registry' if args.registry else 'mTLS consumer source: local workspace')
        mtls_failed = False
finally:
    mtls.close(failed=mtls_failed)
