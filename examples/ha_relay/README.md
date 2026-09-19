# Home Assistant relay consumer

A native MoonBit controller translates Home Assistant ON/OFF requests into short-lived, correlated commands. A separate native simulator implements the device side. Home Assistant, controller and device each connect to the broker; no controller-to-device socket or GPIO is involved.

## Run locally

Start a private Mosquitto broker, then run each command in a separate terminal from the repository root:

```sh
export MQTT_DEMO_HOST=127.0.0.1
export MQTT_DEMO_PORT=1883
export HA_RELAY_PREFIX=moon/ha/relay
export HA_RELAY_ENTITY_ID=moon_relay
./scripts/moon.sh run examples/ha_relay/simulator --target native
./scripts/moon.sh run examples/ha_relay/controller --target native
```

The two run commands are separate long-running processes. The controller and simulator client IDs default to `moon-ha-controller` and `moon-ha-simulator`. Set `HA_RELAY_CONTROLLER_CLIENT_ID` and `HA_RELAY_DEVICE_CLIENT_ID` for additional instances; assign a distinct prefix and entity ID to every relay.

Optional broker settings: `MQTT_DEMO_USERNAME`, `MQTT_DEMO_PASSWORD`, `MQTT_DEMO_TLS=1`, `MQTT_DEMO_CA_FILE`. Credentials are loaded from the environment. Use a broker ACL that limits HA to its command/discovery/state topics, the controller to this prefix and HA birth, and each device to its own command/query/feedback/availability topics. The example does not modify a real broker or HA configuration.

Enable the MQTT integration and discovery in an existing Home Assistant installation connected to that same broker. The controller publishes `homeassistant/switch/<entity>/config` with a state topic, `optimistic=false`, `retain=false` for commands and both device/controller availability required. It republishes discovery on `homeassistant/status=online`. This behavior follows the official [MQTT switch](https://www.home-assistant.io/integrations/switch.mqtt/) and [MQTT discovery/birth](https://www.home-assistant.io/integrations/mqtt/) contracts. A Paho oracle validates the host wire behavior; an actual HA installation has not been exercised here.

## Wire contract

Under the configured prefix:

| Topic | Publisher | Payload / meaning |
|---|---|---|
| `ha/set` | HA | Non-retained `ON` or `OFF` |
| `ha/state` | Controller | Retained `ON`/`OFF` only after fresh correlated feedback; `None` when unknown |
| `device/set` | Controller | JSON `id`, `target`, `expires_at_ms` |
| `device/query` | Controller | JSON `id`; query only, no actuator change |
| `device/feedback` | Device | JSON `boot_id`, integer `sequence`, `state`, `correlation_id` |
| `controller/availability` | Controller / LWT | Retained `online` after fresh feedback; otherwise `offline` |
| `device/availability` | Device / LWT | Retained `online` after subscriptions; `offline` on connection loss |

Command and query IDs use 128 bits from native entropy, and startup fails if entropy is unavailable. The controller sets a five-second immutable Unix-millisecond expiry; the simulator rejects missing, fractional, expired or more-than-30-seconds-ahead deadlines and retained command deliveries. Device clocks must be synchronized: a deadline is not trustworthy across arbitrary wall-clock changes. The simulator remembers at most 64 recent command envelopes, rejects conflicting reuse of an ID, and applies only absolute targets. This is not a permanent deduplication store or exactly-once physical execution guarantee.

The controller permits one unconfirmed command at a time. Further HA requests during that interval are rejected with a diagnostic; they are not accumulated in an unbounded queue. PUBACK only logs broker acceptance. Missing feedback, ambiguous delivery and expiry trigger fresh state queries without replaying the command. A process restart queries the device and does not force a default OFF target. Old retained feedback cannot match the new random query identity. A device that stops answering queries is marked unavailable.

## Verification and hardware handoff

`MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python -W error::ResourceWarning tests/ha_relay.py` runs separate native controller/simulator processes against real Mosquitto, with an independent Paho observer. It checks correlated execution reporting, controller restart, fresh IDs, discovery birth, expiry/retained rejection, PUBACK without device execution, and offline LWT. Native protocol tests cover malformed envelopes and deadlines.

For ESP32, first identify the board, pinout, active level, actuator and power arrangement. Port only the documented device envelope after those details are verified; do not assume a universal onboard LED/GPIO. Real hardware must verify clock synchronization, reboot identity, subscription readiness, expiry rejection, bounded deduplication, offline/LWT and actual observed output. Broker acknowledgement is never the output measurement. No ESP32 firmware, physical output or actual Home Assistant acceptance is claimed by these host tests. Published-registry installation is also a separate release gate.
