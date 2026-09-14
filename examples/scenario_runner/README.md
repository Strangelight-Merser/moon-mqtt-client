# Network scenario fixture

This executable is a finite integration fixture for the MoonBit MQTT client and
the example business contracts. It publishes inputs to topics it has subscribed
to, receives them through an independent MQTT broker, applies the Frigate and
ROS-side contract rules, publishes outputs, receives those outputs, asserts their
exact payloads and disconnects.

Its Frigate fixture uses the documented `events` shape: top-level `type` and an
`after` object containing `id`, `label` and `entered_zones`. The input velocity
command is an application-specific envelope. The fixture converts it to nested
`geometry_msgs/msg/Twist` JSON for a bridge configured with JSON mode and an
explicit ROS type. It does not start or claim integration with a real Frigate
process, ROS graph, camera or robot.

The fixture expects an MQTT 3.1.1 broker at `MQTT_TEST_HOST` and
`MQTT_TEST_PORT`, defaulting to `127.0.0.1:1883`:

```sh
./scripts/moon.sh run examples/scenario_runner
```

Success prints:

```text
scenario fixture passed: Frigate dedup + ROS command/telemetry
```

The run is bounded to ten seconds and uses QoS 1. It fails on rejected
subscriptions, malformed or altered fixture payloads, duplicate Frigate alerts,
connection loss or timeout. MQTT acknowledgement still does not prove any
external ROS or physical-device action.
