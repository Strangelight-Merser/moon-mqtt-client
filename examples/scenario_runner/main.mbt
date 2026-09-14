///|
let frigate_input = "moon/fixtures/frigate/events"

///|
let ros_command_input = "moon/fixtures/ros/command/cmd_vel"

///|
let ros_primitive_input = "moon/fixtures/ros/telemetry/raw/battery_percent"

///|
let alert_output = "moon/fixtures/alerts/person"

///|
let receipt_output = "moon/fixtures/ros/receipt/cmd_vel"

///|
let twist_output = "moon/fixtures/ros/bridge/cmd_vel"

///|
let telemetry_output = "moon/fixtures/ros/telemetry/battery_percent"

///|
suberror QuietPeriodEnded derive(Debug)

///|
fn require(condition : Bool, message : String) -> Unit {
  if !condition {
    abort(message)
  }
}

///|
fn decode_text(payload : Bytes) -> String? {
  Some(@utf8.decode(payload)) catch {
    _ => None
  }
}

///|
fn json_string(object : Map[String, Json], field : String) -> String? {
  match object.get(field) {
    Some(String(value)) => Some(value)
    _ => None
  }
}

///|
fn json_number(object : Map[String, Json], field : String) -> Double? {
  match object.get(field) {
    Some(Number(value, ..)) => Some(value)
    _ => None
  }
}

///|
fn parse_frigate_end(payload : Bytes) -> @contracts.FrigateEvent? {
  let text = match decode_text(payload) {
    Some(value) => value
    None => return None
  }
  let value = @json.parse(text) catch { _ => return None }
  match value {
    Object(object) =>
      match (json_string(object, "type"), object.get("after")) {
        (Some("end"), Some(Object(after))) => {
          let id = json_string(after, "id")
          let label = json_string(after, "label")
          let zones = match after.get("entered_zones") {
            Some(Array(items)) => {
              let result : Array[String] = []
              for item in items {
                match item {
                  String(zone) => result.push(zone)
                  _ => return None
                }
              }
              Some(result)
            }
            _ => None
          }
          match (id, label, zones) {
            (Some(id), Some(label), Some(zones)) =>
              Some({ id, phase: @contracts.End, label, zones, })
            _ => None
          }
        }
        _ => None
      }
    _ => None
  }
}

///|
async fn run_fixture(host : String, port : Int) -> Unit {
  let config = @mqtt.Config::new(
    host,
    "moon-scenario-fixture",
    port~,
    connect_timeout_ms=3000,
    ack_timeout_ms=3000,
    reconnect_attempts=0,
  )
  @mqtt.with_client(config, async fn(client) {
    let subscriptions = [
      @mqtt.Subscription::{ topic: frigate_input, qos: @mqtt.AtLeastOnce, },
      { topic: ros_command_input, qos: @mqtt.AtLeastOnce, },
      { topic: ros_primitive_input, qos: @mqtt.AtLeastOnce, },
      { topic: alert_output, qos: @mqtt.AtLeastOnce, },
      { topic: receipt_output, qos: @mqtt.AtLeastOnce, },
      { topic: twist_output, qos: @mqtt.AtLeastOnce, },
      { topic: telemetry_output, qos: @mqtt.AtLeastOnce, },
    ]
    let granted = client.subscribe(subscriptions)
    require(granted.length() == subscriptions.length(), "wrong SUBACK length")
    for result in granted {
      require(result is @mqtt.Granted(_), "fixture subscription rejected")
    }

    let frigate = "{\"type\":\"end\",\"before\":{\"id\":\"event-17\"},\"after\":{\"id\":\"event-17\",\"label\":\"person\",\"entered_zones\":[\"front_door\"]}}"
    client.publish(frigate_input, @utf8.encode(frigate), qos=@mqtt.AtLeastOnce)
    // Deliberate duplicate: the business rule must produce only one alert.
    client.publish(frigate_input, @utf8.encode(frigate), qos=@mqtt.AtLeastOnce)
    client.publish(
      ros_command_input,
      @utf8.encode(
        "{\"command_id\":\"cmd-42\",\"linear_x\":0.5,\"angular_z\":-0.25}",
      ),
      qos=@mqtt.AtLeastOnce,
    )
    client.publish(
      ros_primitive_input,
      @utf8.encode("73"),
      qos=@mqtt.AtLeastOnce,
    )

    let deduper = @contracts.AlertDeduper::new(16)
    let mut alert_count = 0
    let mut receipt_count = 0
    let mut twist_count = 0
    let mut telemetry_count = 0
    while alert_count + receipt_count + twist_count + telemetry_count < 4 {
      match client.next_event() {
        @mqtt.MessageReceived(message) =>
          if message.topic == frigate_input {
            match parse_frigate_end(message.payload) {
              Some(event) =>
                match deduper.accept(event, "front_door") {
                  Some(alert) =>
                    client.publish(
                      alert_output,
                      @utf8.encode(
                        "{\"event_id\":\"\{alert.event_id}\",\"zone\":\"front_door\"}",
                      ),
                      qos=@mqtt.AtLeastOnce,
                    )
                  None => ()
                }
              None => abort("invalid Frigate fixture returned by broker")
            }
          } else if message.topic == ros_command_input {
            let text = match decode_text(message.payload) {
              Some(value) => value
              None => abort("ROS command was not UTF-8")
            }
            match @contracts.parse_velocity_command(text) {
              Some(command) => {
                require(command.command_id == "cmd-42", "wrong command id")
                require(command.linear_x == 0.5, "wrong linear_x")
                require(command.angular_z == -0.25, "wrong angular_z")
                client.publish(
                  receipt_output,
                  @utf8.encode(
                    @contracts.command_receipt(command.command_id, true),
                  ),
                  qos=@mqtt.AtLeastOnce,
                )
                client.publish(
                  twist_output,
                  @utf8.encode(@contracts.twist_payload(command)),
                  qos=@mqtt.AtLeastOnce,
                )
              }
              None => abort("valid ROS command fixture was rejected")
            }
          } else if message.topic == ros_primitive_input {
            require(
              decode_text(message.payload) == Some("73"),
              "wrong primitive input",
            )
            client.publish(
              telemetry_output,
              @utf8.encode(
                @contracts.primitive_payload(@contracts.IntValue(73)),
              ),
              qos=@mqtt.AtLeastOnce,
            )
          } else if message.topic == alert_output {
            require(
              decode_text(message.payload) ==
              Some("{\"event_id\":\"event-17\",\"zone\":\"front_door\"}"),
              "wrong alert output",
            )
            alert_count += 1
          } else if message.topic == receipt_output {
            require(
              decode_text(message.payload) ==
              Some("{\"command_id\":\"cmd-42\",\"status\":\"accepted\"}"),
              "wrong command receipt",
            )
            receipt_count += 1
          } else if message.topic == telemetry_output {
            require(
              decode_text(message.payload) == Some("73"),
              "wrong telemetry output",
            )
            telemetry_count += 1
          } else if message.topic == twist_output {
            let text = match decode_text(message.payload) {
              Some(value) => value
              None => abort("Twist output was not UTF-8")
            }
            let value = @json.parse(text) catch {
              _ => abort("Twist output was not JSON")
            }
            match value {
              Object(root) =>
                match (root.get("linear"), root.get("angular")) {
                  (Some(Object(linear)), Some(Object(angular))) => {
                    require(
                      json_number(linear, "x") == Some(0.5),
                      "wrong Twist linear.x",
                    )
                    require(
                      json_number(linear, "y") == Some(0.0),
                      "wrong Twist linear.y",
                    )
                    require(
                      json_number(linear, "z") == Some(0.0),
                      "wrong Twist linear.z",
                    )
                    require(
                      json_number(angular, "x") == Some(0.0),
                      "wrong Twist angular.x",
                    )
                    require(
                      json_number(angular, "y") == Some(0.0),
                      "wrong Twist angular.y",
                    )
                    require(
                      json_number(angular, "z") == Some(-0.25),
                      "wrong Twist angular.z",
                    )
                    twist_count += 1
                  }
                  _ => abort("Twist output lacks linear/angular objects")
                }
              _ => abort("Twist output must be an object")
            }
          }
        @mqtt.Disconnected(_, reason) =>
          abort("fixture disconnected: \{reason}")
        @mqtt.Connected(_) => ()
      }
    }
    // Give a duplicate alert a short chance to surface before asserting once.
    @async.with_timeout(250, error=QuietPeriodEnded, () => {
      while true {
        match client.next_event() {
          @mqtt.MessageReceived(message) if message.topic == alert_output =>
            abort("duplicate Frigate alert")
          _ => ()
        }
      }
    }) catch {
      QuietPeriodEnded => ()
      error => raise error
    }
    require(alert_count == 1, "expected exactly one Frigate alert")
    require(receipt_count == 1, "expected one ROS command receipt")
    require(twist_count == 1, "expected one Twist bridge output")
    require(telemetry_count == 1, "expected one primitive telemetry output")
    client.disconnect()
    println("scenario fixture passed: Frigate dedup + ROS command/telemetry")
  })
}

///|
async fn main {
  let host = @env.get_env_var("MQTT_TEST_HOST").unwrap_or("127.0.0.1")
  let port = match @env.get_env_var("MQTT_TEST_PORT") {
    Some(value) => @string.parse_int(value)
    None => 1883
  }
  @async.with_timeout(10000, () => run_fixture(host, port))
}
