#!/usr/bin/env python3
"""Local, explicitly pinned ESP demo. No publication, provisioning or GPIO writes."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process(pid):
    row = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=,command="],
                         capture_output=True, text=True)
    return row.stdout.strip() if row.returncode == 0 else None


def record(pid, binary):
    return {"pid": pid, "identity": process(pid), "binary": str(binary),
            "sha256": digest(binary)}


def stop_owned(owner):
    actual = process(owner["pid"])
    if actual is None:
        return
    if actual != owner["identity"] or digest(owner["binary"]) != owner["sha256"]:
        raise RuntimeError("PID ownership changed; refusing to stop process")
    os.kill(owner["pid"], signal.SIGTERM)
    for _ in range(100):
        if process(owner["pid"]) is None:
            return
        time.sleep(.1)
    raise RuntimeError("Controller did not stop; no broad kill attempted")


def check(config):
    root = config["workspace"]
    for ref, key in [("HEAD", "source_commit"), ("HEAD^{tree}", "source_tree")]:
        got = subprocess.check_output(["git", "-C", root, "rev-parse", ref], text=True).strip()
        if got != config[key]:
            raise RuntimeError("Source identity mismatch: " + key)
    if subprocess.check_output(["git", "-C", root, "status", "--porcelain"], text=True):
        raise RuntimeError("Pinned workspace has uncommitted changes")
    for key in ("binary", "library"):
        if digest(config[key]) != config[key + "_sha256"]:
            raise RuntimeError(key + " hash mismatch")
    print(json.dumps({k: config[k] for k in ("source_commit", "source_tree", "binary_sha256",
                     "library_sha256", "consumer_source", "mode", "firmware_binding")},
                     ensure_ascii=False), flush=True)
    print("logical_state_no_gpio | Release HOLD", flush=True)


def query(broker):
    import paho.mqtt.client as mqtt
    result = []
    subscribed = threading.Event()
    received = threading.Event()
    ident = "month-end-query-" + uuid.uuid4().hex
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=ident)
    client.username_pw_set(broker["host_username"], broker["host_password"])
    client.tls_set(ca_certs=broker["ca_file"])
    def connected(c, u, f, reason, properties):
        if reason == 0:
            c.subscribe(broker["prefix"] + "/device/feedback", 1)
    def message(c, u, m):
        body = json.loads(m.payload)
        if not m.retain and body.get("correlation_id") == ident:
            result.append(body)
            received.set()
    client.on_connect = connected
    client.on_subscribe = lambda *args: subscribed.set()
    client.on_message = message
    try:
        client.connect("127.0.0.1", broker["port"], 15)
        client.loop_start()
        if not subscribed.wait(10):
            raise RuntimeError("Query subscription failed")
        client.publish(broker["prefix"] + "/device/query", json.dumps({"id": ident}), qos=1).wait_for_publish(5)
        if not received.wait(10):
            raise RuntimeError("No fresh device response; state unknown")
        print(json.dumps({"event": "fresh_query", "at": time.time(), "query_id": ident,
                          "feedback": result[0]}, ensure_ascii=False), flush=True)
        return result[0]
    finally:
        client.disconnect()
        client.loop_stop()


def spawn(config, broker, binary, log):
    env = {**os.environ, "MQTT_DEMO_HOST": "127.0.0.1", "MQTT_DEMO_PORT": str(broker["port"]),
           "MQTT_DEMO_TLS": "1", "MQTT_DEMO_CA_FILE": broker["ca_file"],
           "MQTT_DEMO_USERNAME": broker["host_username"], "MQTT_DEMO_PASSWORD": broker["host_password"],
           "HA_RELAY_PREFIX": broker["prefix"], "HA_RELAY_ENTITY_ID": "moon_esp32",
           "HA_RELAY_CONTROLLER_CLIENT_ID": "moon-esp32-controller"}
    with Path(log).open("ab") as stream:
        child = subprocess.Popen([binary], env=env, stdout=stream, stderr=subprocess.STDOUT,
                                 start_new_session=True)
    time.sleep(.5)
    if child.poll() is not None:
        raise RuntimeError("Controller exited; inspect " + str(log))
    return record(child.pid, binary)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("action", choices=["doctor", "query", "start", "stop", "restart"])
    args = ap.parse_args()
    config = json.loads(args.config.read_text())
    check(config)
    if args.action == "doctor":
        return
    broker = json.loads(Path(config["broker_config"]).read_text())
    if args.action == "query":
        query(broker)
        return
    statepath = Path(config["state_file"])
    with statepath.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(statepath.read_text())
        # Enumerate only the two registered executables, and reject unknown ownership.
        lines = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True).splitlines()
        live = {int(row.strip().split(None, 1)[0]) for row in lines if row.strip() and
                len(row.strip().split(None, 1)) == 2 and
                row.strip().split(None, 1)[1] in (config["binary"], config["original_binary"])}
        owner = state.get("owner")
        if live and (not owner or live != {owner["pid"]} or process(owner["pid"]) != owner["identity"]):
            raise RuntimeError("Unregistered controller owner; refusing handoff")
        target = "original" if args.action == "stop" else "candidate"
        if owner and process(owner["pid"]) and state["kind"] == target and args.action != "restart":
            print("Already running: " + target)
            query(broker)
            return
        if owner:
            stop_owned(owner)
        state = {"kind": target, "owner": None}
        statepath.write_text(json.dumps(state, indent=2) + "\n")
        binary = config["original_binary"] if target == "original" else config["binary"]
        if target == "original" and digest(binary) != config["original_binary_sha256"]:
            raise RuntimeError("Original binary changed; refusing restore")
        log = statepath.parent / (target + "-controller.log")
        state["owner"] = spawn(config, broker, binary, log)
        statepath.write_text(json.dumps(state, indent=2) + "\n")
        print(json.dumps({"controller": target, "pid": state["owner"]["pid"], "log": str(log)}))
        query(broker)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
