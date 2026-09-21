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

## Published consumer handoff / 已发布包交接

这条入口只准备并构建已发布的 Mooncakes `Strangelight-Merser/moon-mqtt-client@0.7.1` consumer。脚本先用最小 bootstrap 触发依赖下载，再从输出目录的 `.mooncakes` 复制并核对 R8 冻结的 controller entrypoint；它不启动 broker、Home Assistant、simulator，也不访问 GPIO、串口或真实硬件。

The output must be a new directory outside every ancestor containing `moon.work`:

```sh
export MOON_HOME="$PWD/.tools/moon"  # pinned toolchain installed in this checkout
PARENT="$(mktemp -d /tmp/moon-ha-consumer.XXXXXX)"
OUT="$PARENT/v071"
MOONBIT_ASYNC_CHECK_FD_LEAK=1 python3 scripts/prepare-ha-consumer.py "$OUT"
cat "$OUT/consumer-manifest.json"
```

`OUT` must not already exist and the output must not contain credentials. A successful manifest records the fetched dependency name/version and `.mooncakes` source, the frozen controller hashes, and the native binary hash. The result line is `HOST_CONSUMER_BUILD=PASS; HIL=NOT_RUN`; it verifies fetching and building the registry package, not runtime, HA UI behavior, or physical output.

## Hardware inputs / 实机输入

Fill these values from the actual handoff before connecting a device. Keep credentials outside Git.

| Input | Required value / 必填值 |
|---|---|
| Board | Exact board, chip revision, flash/PSRAM size, and board label / 板型、芯片修订、Flash/PSRAM、板上标识 |
| Firmware | Image SHA256, build date, FQBN, Arduino/core/library versions, and whether this exact image was live-validated / 固件哈希、构建日期、FQBN、版本，并注明是否就是实测镜像 |
| Output circuit | Actuator/relay/LED, GPIO, active level, power/common ground, safe OFF behavior; never assume an onboard LED / 执行器、GPIO、有效电平、电源共地、安全断电状态 |
| Serial/reset | Serial path, baud, reset/boot behavior, and log destination / 串口、波特率、复位启动行为、日志位置 |
| Network/time | Wi-Fi auth mode, SSID identifier, 2.4 GHz reachability, DHCP/SNTP/clock status / 认证方式、SSID 标识、2.4 GHz 可达性、DHCP/SNTP/时钟 |
| Broker | Host/IP, port, TLS CA/hostname, ACL identity/topic scope, and restart procedure / 地址端口、TLS CA/主机名、ACL 身份与 topic 范围、重启方法 |
| Home Assistant | Version/URL, MQTT identity, entity ID, and UI-vs-service action used / 版本地址、MQTT 身份、实体 ID、实际 UI 与 service 操作 |
| Safety | Power isolation, stop procedure, test duration, and responsible person / 断电隔离、停止方法、测试时长、负责人 |

## Acceptance matrix / 验收矩阵

| Check | Evidence required / 所需证据 | Current boundary / 当前边界 |
|---|---|---|
| Published consumer | Mooncakes `0.7.1` fetched, clean native build, manifest and hashes | Registry consumer build `HOST_PASS`; runtime/HIL are separate |
| Cold boot and query | `boot_id`, clock/TLS/MQTT readiness, fresh query ID and matching feedback | Historical host/device evidence exists; rerun against the exact final image |
| Real HA UI ON/OFF | Browser clicks, command ID, serial applied count, `correlation_id`, reported state and HA state | API/service evidence cannot substitute for UI; current UI evidence is `INCOMPLETE` |
| Device feedback | `boot_id`, sequence, state and correlation across serial/MQTT | Record each transition and distinguish logical state from output |
| Device reset/LWT | Offline availability, nonoptimistic unknown state, and no false ON after reset | Must be observed on the actual device and broker |
| Retained command | Retained `device/set` is rejected and produces no actuator change | Earlier firmware passed a device check; final firmware still needs verification |
| Controller restart | Fresh query, no replay of `device/set`, and restored reported state | Required in the final hardware run |
| Broker restart | Fresh query, no command replay, and availability transitions | Required in the final hardware run |
| Unknown query | Read-only fresh query ID and feedback; no physical-state inference | Report logical feedback separately from physical output |
| Physical output | Independent observation of relay/LED/actuator and safe OFF | `HIL_NOT_RUN` while output circuit/GPIO is unverified; `logical_state_no_gpio` is not physical proof |

For each row record local time with offset, `boot_id`, sequence, command/query correlation and expiry, topic/retain flags, serial applied count, controller event, HA availability/state, and an independent physical observation. Keep `HOST_PASS` and `HIL_NOT_RUN` as separate labels.

## Verification and hardware handoff

`MOONBIT_ASYNC_CHECK_FD_LEAK=1 .venv/bin/python -W error::ResourceWarning tests/ha_relay.py` runs separate native controller/simulator processes against real Mosquitto, with an independent Paho observer. It checks correlated execution reporting, controller restart, fresh IDs, discovery birth, expiry/retained rejection, PUBACK without device execution, and offline LWT. Native protocol tests cover malformed envelopes and deadlines.

For ESP32, first identify the board, pinout, active level, actuator and power arrangement. Port only the documented device envelope after those details are verified; do not assume a universal onboard LED/GPIO. Real hardware must verify clock synchronization, reboot identity, subscription readiness, expiry rejection, bounded deduplication, offline/LWT and actual observed output. Broker acknowledgement is never the output measurement. No ESP32 firmware, physical output or actual Home Assistant acceptance is claimed by these host tests. Published-registry installation is also a separate release gate.

### Existing local evidence boundary / 已有本地证据边界

The following local files are historical references only; do not copy credentials or treat them as current final-image proof:

- `/Users/huaiyi/Documents/ESP32-campus/STOPPED.md`
- `/Users/huaiyi/Documents/ESP32-campus/ha-hil-run1/results.json`
- `/Users/huaiyi/Documents/ESP32-campus/hotspot-hil-rx-fixed/results.json`
- `/Users/huaiyi/Documents/ESP32-campus/ha-ui-results.json`

They separate HA service/API checks from browser UI and physical-output evidence. Earlier successful runs cannot certify a later final awake firmware image. The UI result remains `INCOMPLETE`, physical output remains `HIL_NOT_RUN` where no circuit was independently observed, and `logical_state_no_gpio` describes a logical state only.
