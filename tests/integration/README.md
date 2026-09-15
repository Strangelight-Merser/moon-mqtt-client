# Integration tests

These tests start an isolated Mosquitto process on an ephemeral loopback port.
Paho is the independent peer/oracle. The packet proxy can fragment broker
packets or terminate a connection before PUBACK reaches the MoonBit client.

Run all implemented black-box cases with:

```sh
./tests/integration/run.sh
```

Mosquitto and OpenSSL must be on `PATH` (or set `MOSQUITTO` explicitly). Install
the Python oracle with `python3 -m pip install -r tests/integration/requirements.txt`.

The suite currently covers plain TCP QoS 0/1 in both directions, custom-CA TLS
including hostname and trust rejection, broker restart with resubscription, and
a lost-PUBACK request that must never be reported as successful. It also checks
correct and incorrect username/password authentication, an ACL-denied
subscription, fragmented packets, heartbeat failure, Will behavior, retained
delivery and
clearing, and UNSUBACK followed by an independent no-delivery window. It is an
integration suite, not an MQTT conformance claim.

On failure the temporary broker directory is printed and retained. Successful
runs clean it up.

## Driver protocol

`examples/test_driver` selects a case with `MQTT_TEST_SCENARIO`. It emits one
JSON object per stdout line; build logs and diagnostics belong on stderr.
Required events are documented by the assertions in `run.py`.
