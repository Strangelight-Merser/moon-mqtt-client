# moon-mqtt-client

[![native client](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/workflows/check.yml/badge.svg)](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/workflows/check.yml)

A native asynchronous MQTT 3.1.1 client for MoonBit. Connect to an existing MQTT
broker, subscribe to device or application events, and publish commands and
state without writing a socket loop for each application.

The packet codec is provided by **zbhzs1/moonbit-mqtt**; this package supplies the
connection lifecycle, TLS transport, request tracking, heartbeat and clean-session
reconnection on top of **moonbitlang/async**. It is an early implementation, not
a certified MQTT conformance implementation.

## Start with the state-sync demo

The primary demonstration is a controller process and an **independent simulated
device** process (no hardware; the device is a simulation and is labelled as one)
talking to a real broker:

```sh
./scripts/moon.sh build --target native
.venv/bin/python examples/mqtt_demo/demo.py
```

Four reproducible scenarios run in sequence: normal ON/OFF, a lost PUBACK whose
command the device still executes, a broker restart, and a controller restart
while the device stays ON. Every line of output keeps three things apart:
`desired` (what hysteresis wants), `result` (`sent`/`not_sent`/`unknown`), and
`reported` (what the device actually says, correlated by id). See
[docs/SCENARIOS.md](docs/SCENARIOS.md) for the topics and rules.

A thin publish/subscribe CLI reuses the public API:

```sh
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  publish --host 127.0.0.1 -t lab/state -m ON --qos 1 --retain --stats
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  subscribe --host 127.0.0.1 -t 'lab/#' --count 1 --timeout-ms 3000
```

## Scope

- Native TCP and server-authenticated TLS (system roots or a custom PEM CA).
- MQTT 3.1.1, QoS 0 and 1, retained messages, Last Will, username/password.
- Subscribe and unsubscribe acknowledgements, including per-topic rejection.
- Bounded send queue, event queue, packet size and concurrent requests.
- CleanSession=true only: reconnect creates a new session and restores confirmed
  subscriptions. A `Connected(generation)` event follows subscription restoration.
- Callback-scoped tasks and sockets. Normal callback return or `disconnect()` sends
  DISCONNECT; callback failure or cancellation closes the transport abruptly.

No QoS 2, MQTT 5, persistent sessions, offline queue, cross-connection retransmit,
client-certificate authentication, WebSocket, browser or microcontroller target.

## Build from source

Use the native MoonBit toolchain; this checkout was developed with
`moon 0.1.20260904` and `moonc v0.10.12+1634b282e` (2026-09-07).

```sh
moon update
moon check --target native
moon test --target native
moon build --target native
```

`scripts/moon.sh` uses a local `.tools/moon` installation or `MOON_HOME` when
provided. Toolchains and build outputs are not part of the source distribution.
For registry releases, install the package with:

```sh
moon add Strangelight-Merser/moon-mqtt-client
```

See [GitHub Releases](https://github.com/Strangelight-Merser/moon-mqtt-client/releases)
for available versions and their verification results.

## API

The following is the callback shape used by the runnable examples. Import this
package as `@mqtt` and `moonbitlang/core/encoding/utf8` as `@utf8`.

```moonbit
async fn main {
  let config = @mqtt.Config::new("127.0.0.1", "my-moon-client")
  @mqtt.with_client(config, async fn(client) {
    let results = client.subscribe([
      { topic: "lab/temperature", qos: @mqtt.AtLeastOnce },
    ])
    if results == [@mqtt.Granted(@mqtt.AtLeastOnce)] {
      client.publish("lab/status", @utf8.encode("ready"),
        qos=@mqtt.AtLeastOnce, retain=true)
    }
    // Consume Connected, Disconnected and MessageReceived events here.
    // A long-running subscriber must continually drain next_event().
  })
}
```

`with_client` waits for the first successful connection before invoking the
callback. An initial connection/CONNACK/TLS failure is returned to the caller.
After a connection has been established, failures trigger bounded retries.
`wait_connected()` can wait through a reconnect; publish/subscribe/unsubscribe
while disconnected fail with `NotConnected`, rather than entering an offline queue.

Use `TlsMode::SystemRoots` with port 8883 for public trust or
`TlsMode::CustomCA("path/to/ca.pem")` for a private CA. The configured host is also
the verified TLS hostname. There is no option to disable verification.

## Delivery and failure semantics

The normative description of timeouts, queueing, cancellation and failure
classification is [docs/API-CONTRACT.md](docs/API-CONTRACT.md). In short:

| Result | What it establishes |
|---|---|
| QoS 0 publish returns | The transport write completed; no broker acknowledgement exists. |
| QoS 1 publish returns | A matching PUBACK arrived on that connection. It does not establish downstream processing or a physical action. |
| `NotSent` | A queued request failed before its write started. |
| `OutcomeUnknown` | A write began but the operation was not confirmed. Partial writes and lost ACKs are included. |
| `Backpressure` from a request | The send or inflight limit prevented accepting that request. |
| Event/control queue overflow | The client terminates with an error instead of silently dropping messages. |

The whole operation is budgeted by `operation_timeout_ms` (default 5000 ms),
covering queue time, the write and the wait for the acknowledgement. A single
socket write is separately bounded by `write_timeout_ms`, and PINGRESP is
timed from the *completed* PINGREQ write. An expired operation budget closes the
connection and ends **all** pending requests; old identifiers never cross into
the new connection. The application chooses whether to retry an uncertain
operation; use idempotent state-setting commands or application command IDs where
appropriate.

Protocol control packets (PUBACK, PINGREQ, DISCONNECT) use reserved bounded slots
and are never blocked behind a full business queue. If the control slots
themselves run out, the client terminates loudly instead of dropping one.

Incoming QoS 1 is acknowledged after acceptance into the bounded event queue,
not after application processing. The queue is volatile. QoS 1 duplicates are
possible. Reconnection with a clean session can lose messages during the gap;
a broker may replay retained state on resubscription. The library does not promise
exactly-once processing, durable delivery or uninterrupted subscriptions.

Default limits: 64 queued business sends, 16 reserved control slots, 128 queued
events, 32 pending operations, 65,536 bytes per packet, 5-second connect,
operation, write and PINGRESP timeouts, 30-second keepalive, 10 consecutive
reconnect attempts with a delay growing from 250 ms to 5 seconds plus per-client
jitter. `Client::set_reconnect_seed` pins that jitter for tests. Confirmed
subscription filters remain in memory until unsubscribed; keep their set bounded
in the application.

`Client::stats()` returns a read-only snapshot: generation, connection state,
business/control/event queue occupancy, pending requests, reconnects, disconnects,
unknown outcomes and the most recent disconnect reason. It contains no
credentials and no message bodies and needs no monitoring service.

## Runnable scenarios

中文入口：[从这里开始](docs/START_HERE.zh-CN.md)。

See [docs/SCENARIOS.md](docs/SCENARIOS.md) for complete inputs, rules, outputs and
failure boundaries for three intended uses:

1. Temperature control with hysteresis, retained state and availability.
2. A bounded Frigate event deduplicator which alerts on completed person events.
3. A ROS bridge JSON/primitive contract with command validation and receipts.

The examples use fixtures. They do not claim a deployed camera, robot or Zigbee
integration. The Frigate and ROS contracts remain supplementary adapter
contracts; their tests reject non-finite speeds, empty command ids and
quote/backslash/control-character payloads, and every emitted payload is JSON.

## Verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r tests/integration/requirements.txt
# Install Mosquitto and OpenSSL using your OS package manager, then:
PYTHON=.venv/bin/python ./tests/integration/run.sh
```

Run every local check with `./scripts/check.sh`. Two heavier harnesses are run
separately and are recorded in [docs/VALIDATION.md](docs/VALIDATION.md):

```sh
# Pinned official EMQX image: send/receive, subscription denial, restart recovery
./tests/emqx.sh

# 30-minute restart soak: 1 KiB QoS 1, concurrency 16, 100 disconnect cycles.
# The broker log is quiet by default and capped, so the run leaves bounded evidence.
MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python tests/soak.py \
  --duration 1800 --cycles 100 --artifacts tests/integration/artifacts/soak
```

The integration suite uses independent Mosquitto and Eclipse Paho processes,
loopback-only listeners, temporary certificates and packet fault injection.
See [docs/VALIDATION.md](docs/VALIDATION.md) for the actual executed results and
remaining gaps. A workflow definition is not evidence of a completed hosted CI run.

## License and upstream work

Apache-2.0. See [NOTICE](NOTICE). The dependencies remain separate packages with
their own attribution and licenses; this project does not claim authorship of
MQTT wire encoding, the async runtime or TLS implementation.
