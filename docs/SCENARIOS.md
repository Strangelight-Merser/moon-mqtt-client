# Scenario contracts

These examples are contract tests against ordinary MQTT topics. They contain
real application rules, but use public-format simulated inputs so that no
thermostat, camera, Frigate process or ROS installation is required.

The client always starts a clean MQTT 3.1.1 session. After a disconnect it opens
a new session and restores subscriptions. Messages can be missing during the
gap. A QoS 1 PUBACK confirms the MQTT handshake only; it does not prove that an
actuator, alert consumer or robot executed the business action. A request that
was written before a disconnect can finish as `OutcomeUnknown` and is not
replayed automatically.

## State-sync thermostat controller and device (primary scenario)

This is the runnable end-to-end demonstration: a controller process, an
independent **simulated** device process, and a separate Paho observer, all
talking to a real Mosquitto broker. There is no hardware; the device is a
simulation and is labelled as one everywhere it appears.

Topics (default prefix `moon/demo/thermostat`, configurable with
`MQTT_DEMO_TOPIC_PREFIX`):

| Topic | Direction | Payload | Retained |
| --- | --- | --- | --- |
| `<prefix>/temperature` | controller input | `{"temperature_c":29.2}` | no |
| `<prefix>/relay/set` | controller output | `{"command_id":"cmd-1","target":"ON","reason":"temperature"}` | no |
| `<prefix>/relay/query` | controller output | `{"query_id":"qry-1"}` | no |
| `<prefix>/relay/feedback` | device output | `{"startup_id":"dev-...","sequence":3,"state":"ON","command_id":"cmd-1","query_id":null}` | yes |
| `<prefix>/device/availability` | device output | `online`/`offline` | yes |

Rules:

- The controller keeps only a **desired** target. `reported` is `None` until
  device feedback confirms a state; nothing is ever reported as "executed"
  before that.
- Startup and every reconnect begin with a query. Only feedback carrying a
  query id this controller just issued (or the command id it just sent) updates
  `reported`. A retained payload from an earlier run is reported as
  `feedback_ignored` and is not treated as current truth.
- Thresholds are 28 C on and 26 C off. Inside the deadband the last known target
  is held. If the relay state is unknown inside the deadband, the controller
  emits `uncertain` and queries instead of assuming OFF (this replaces the old
  `relay_on = false` default).
- Commands carry an id and an absolute target and are not retained. The device is
  idempotent: applying `ON` to an ON device is a no-op, and a repeated command id
  still produces feedback for correlation.
- After a publish whose outcome is `unknown` (a lost PUBACK), the controller does
  not resend blindly and does not claim success: it queries, compares the answer
  with the desired target, and only then decides whether another absolute command
  is needed.

Reproduce all four scenarios with one command (build first):

```sh
./scripts/moon.sh build --target native
.venv/bin/python examples/mqtt_demo/demo.py
```

| Scenario | What it proves |
| --- | --- |
| `normal` | 29 C -> ON confirmed by feedback, 25 C -> OFF confirmed, 27 C holds |
| `lost_puback` | The PUBACK is discarded while the device still executes; the controller reports `unknown`, reconnects, queries, and re-aligns to ON |
| `broker_restart` | Both processes reconnect, the controller re-queries, and stale retained feedback is ignored |
| `controller_restart` | The controller restarts while the device is still ON; the fresh controller queries and learns ON without trusting the retained payload |

Logs are one JSON object per line and keep the three notions separate:
`desired`, `result` (`sent`/`not_sent`/`unknown`/`none`), and `reported`.

### Thin publish/subscribe CLI

`examples/mqtt_demo/cli` reuses the public API and supports QoS, retain, TLS, a
custom CA, message counting and a `--stats` diagnostic dump:

```sh
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  publish --host 127.0.0.1 -t lab/state -m ON --qos 1 --retain --stats
./scripts/moon.sh run examples/mqtt_demo/cli --target native -- \
  subscribe --host 127.0.0.1 -t 'lab/#' --count 1 --timeout-ms 3000
```

Credentials are read from `MQTT_DEMO_USERNAME`/`MQTT_DEMO_PASSWORD` and are never
printed. Unknown options and an unsupported `--qos` fail loudly with a non-zero
exit status.

## Frigate event lifecycle alert

Input topic: `frigate/events`. Frigate's documented event payload has top-level
`type` and `before`/`after` objects. For an end event, the adapter reads
`after.id`, `after.label` and `after.entered_zones` into
`FrigateEvent { id, phase, label, zones }`; fields not needed by this rule are
ignored. The rule emits an alert only when all conditions hold:

1. the phase is `End`;
2. the label is exactly `person`;
3. `zones` contains the configured target zone; and
4. that event id is absent from the recent-alert window.

The suggested output topic is `demo/alerts/person`, with a payload containing
the event id and target zone. Repeated `End` messages for the same id emit once
while the id remains in the window. `AlertDeduper::new(capacity)` stores at most
`capacity` ids; after eviction, replay of an old id can alert again. This bound is
intentional and makes memory use explicit rather than promising permanent or
exactly-once deduplication. `New` and `Update` do not alert, and non-person or
out-of-zone events are ignored.

## ROS bridge telemetry, command and receipt

This is an MQTT-side wire contract. It does not claim a running ROS node, ROS
middleware integration, topic type discovery or robot execution.

Primitive telemetry maps a known ROS-facing value to a UTF-8 MQTT payload:
booleans (`true`/`false`), integers, doubles and text. An adapter can publish it
under `demo/ros/telemetry/<name>`. The application-specific velocity envelope
arrives on `demo/ros/command/cmd_vel` as:

```json
{"command_id":"cmd-9","linear_x":0.4,"angular_z":-0.2}
```

`command_id`, `linear_x` and `angular_z` are mandatory and type checked. Invalid
JSON, missing fields, an empty `command_id` and strings in numeric fields are
rejected. Non-finite speeds are rejected too: `1e999` parses to `+inf` and a NaN
would compare false against every bound, so both are refused before reaching a
device. Velocity *ranges* are a device concern and are intentionally not enforced
by the generic contract. The adapter
converts an accepted envelope to the nested JSON representation of
`geometry_msgs/msg/Twist`:

```json
{"linear":{"x":0.4,"y":0,"z":0},"angular":{"x":0,"y":0,"z":-0.2}}
```

To forward this payload, the external MQTT/ROS bridge contract requires JSON
mode (`json: true`) and `ros_type: geometry_msgs/msg/Twist`. The receipt published to
`demo/ros/receipt/cmd_vel` is either:

```json
{"command_id":"cmd-9","status":"accepted"}
```

or the same object with `"status":"rejected"`. “Accepted” means that the
contract adapter accepted the command for forwarding. It is not evidence of ROS
delivery or physical motion. Applications needing proof must add a separate ROS
feedback-to-MQTT telemetry contract.

## Pure contract tests

Run all three rule sets without a broker:

```sh
./scripts/moon.sh test -p Strangelight-Merser/moon-mqtt-client/examples/contracts
```

The tests cover threshold inclusivity and deadband state, the `Uncertain` result
for an unknown state inside the deadband, availability payloads, Frigate
lifecycle/label/zone filtering, duplicate suppression and capacity, plus
primitive serialization (including `null` for non-finite doubles), strict JSON
command decoding (empty ids, `1e999`, NaN), quoting/backslash/control-character
escaping, Twist conversion and receipts.
