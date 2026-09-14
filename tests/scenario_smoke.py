#!/usr/bin/env python3
"""Run business fixtures against Mosquitto; no actual device/Frigate/ROS assumed."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mqtt_integration', ROOT / 'tests/integration/run.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def until(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.025)
    raise AssertionError('expected broker observation did not arrive')


subprocess.run([str(h.MOON), 'build', '--target', 'native'], cwd=ROOT, check=True)
broker = h.Broker()
broker.start()
client = None
controller = None
failed = True
try:
    env = {**os.environ, 'MQTT_TEST_PORT': str(broker.port)}
    subprocess.run([str(h.MOON), 'run', 'examples/scenario_runner'], cwd=ROOT,
                   env=env, check=True, timeout=20)
    messages = []
    subscribed = threading.Event()
    client = h.paho.Client(h.paho.CallbackAPIVersion.VERSION2, client_id='scenario-observer')
    client.on_connect = lambda c, u, f, r, p: c.subscribe('demo/thermostat/#', 1)
    client.on_subscribe = lambda *args: subscribed.set()
    client.on_message = lambda c, u, m: messages.append((m.topic, m.payload, m.retain))
    client.connect('127.0.0.1', broker.port)
    client.loop_start()
    assert subscribed.wait(3)
    controller = subprocess.Popen([str(h.MOON), 'run', 'examples/temperature_controller'],
                                  cwd=ROOT, env=env, start_new_session=True,
                                  stdout=subprocess.DEVNULL)
    # Retained input avoids losing the first fixture while SUBSCRIBE starts.
    client.publish('demo/thermostat/temperature', b'{"temperature_c":28}', 1, True).wait_for_publish(3)
    commands = lambda: [v for t, v, _ in messages if t == 'demo/thermostat/relay/set']
    until(lambda: commands() == [b'ON'])
    for payload in (b'{"temperature_c":27}', b'{bad json', b'\xff'):
        client.publish('demo/thermostat/temperature', payload, 1).wait_for_publish(3)
    time.sleep(.3)
    assert commands() == [b'ON'], commands()
    client.publish('demo/thermostat/temperature', b'{"temperature_c":26}', 1).wait_for_publish(3)
    until(lambda: commands() == [b'ON', b'OFF'])
    until(lambda: any(t.endswith('/relay/state') and p == b'OFF' for t,p,_ in messages))
    os.killpg(controller.pid, signal.SIGTERM)
    controller.wait(3)
    until(lambda: any(t.endswith('/availability') and p == b'offline' for t,p,_ in messages))
    print('temperature fixture passed: ON, deadband/invalid ignored, OFF, offline Will')
    failed = False
finally:
    if controller is not None:
        try:
            os.killpg(controller.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        controller.wait()
    if client is not None:
        client.disconnect()
        client.loop_stop()
    broker.close(failed=failed)
