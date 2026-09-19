#!/usr/bin/env python3
"""Codec-only native MQTT 5 seam; no production-runtime acceptance claim."""
import importlib.util
from pathlib import Path
import subprocess
import threading
from paho.mqtt.properties import Properties
from paho.mqtt.packettypes import PacketTypes
from build_paths import demo_build_dir

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration", ROOT / "tests/integration/run.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def main():
    subprocess.run([str(h.MOON), "build", "--target", "native"], cwd=ROOT, check=True)
    broker = h.Broker()
    client = None
    try:
        broker.start()
        ready = threading.Event()
        received = []
        errors = []
        client = h.paho.Client(h.paho.CallbackAPIVersion.VERSION2, client_id="paho5-probe", protocol=h.paho.MQTTv5)
        client.on_connect = lambda c,u,f,r,p: c.subscribe("codec/request", 1)
        client.on_subscribe = lambda *args: ready.set()
        def on_message(c,u,m):
            try:
                assert m.payload == b"native5"
                assert m.properties.ResponseTopic == "codec/reply"
                assert m.properties.CorrelationData == b"\x00\xff"
                assert m.properties.UserProperty == [("origin", "native")]
                assert 0 < m.properties.MessageExpiryInterval <= 30
                properties = Properties(PacketTypes.PUBLISH)
                properties.CorrelationData = m.properties.CorrelationData
                properties.UserProperty = [("origin", "paho")]
                c.publish(m.properties.ResponseTopic, b"paho5", qos=1, properties=properties)
                received.append(m.payload)
            except BaseException as error:
                errors.append(repr(error))
        client.on_message = on_message
        client.connect("127.0.0.1", broker.port)
        client.loop_start()
        assert ready.wait(5), "Paho SUBACK missing"
        binary = demo_build_dir(ROOT).parent / "mqtt5_probe/mqtt5_probe.exe"
        result = subprocess.run([str(binary), str(broker.port)], text=True, capture_output=True, timeout=15)
        assert result.returncode == 0, (result.stdout, result.stderr, errors)
        assert received == [b"native5"], received
        assert not errors, errors
        print(result.stdout.strip())
    finally:
        if client:
            client.disconnect()
            client.loop_stop()
        broker.stop()


if __name__ == "__main__":
    main()
