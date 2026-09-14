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

## Temperature hysteresis controller

The runnable CLI in `examples/temperature_controller` connects to
`127.0.0.1:1883` and uses these topics:

| Topic | Direction | Payload | Retained |
| --- | --- | --- | --- |
| `demo/thermostat/temperature` | input | `{"temperature_c":29.2}` | no requirement |
| `demo/thermostat/relay/set` | output | `ON` or `OFF` | no |
| `demo/thermostat/relay/state` | output receipt/state | `ON` or `OFF` | yes |
| `demo/thermostat/availability` | output | `online` or `offline` | yes |

An off relay turns on at a temperature greater than or equal to 28 C. An on
relay turns off at a temperature less than or equal to 26 C. Values strictly
inside that deadband preserve the current state and publish no actuator command.
Malformed JSON or a missing/non-numeric `temperature_c` field is ignored.

The CLI publishes retained `online` after connecting. Its MQTT Will is retained
`offline`, so the broker publishes it only after an abnormal connection loss;
normal `DISCONNECT` does not trigger the Will. Relay state is a state report, not
proof that physical hardware moved.

With Mosquitto running locally:

```sh
./scripts/moon.sh run examples/temperature_controller
mosquitto_sub -t 'demo/thermostat/#' -v
mosquitto_pub -t demo/thermostat/temperature -m '{"temperature_c":28}' -q 1
mosquitto_pub -t demo/thermostat/temperature -m '{"temperature_c":27}' -q 1
mosquitto_pub -t demo/thermostat/temperature -m '{"temperature_c":26}' -q 1
```

The observable actions are one `ON`, no command for 27 C, then one `OFF`.

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
JSON, missing fields and strings in numeric fields are rejected. The adapter
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
./scripts/moon.sh test -p huaiyi/moon-mqtt-client/examples/contracts
```

The tests cover threshold inclusivity and deadband state, availability payloads,
Frigate lifecycle/label/zone filtering, duplicate suppression and capacity, plus
primitive serialization, strict JSON command decoding, Twist conversion and
receipts.
