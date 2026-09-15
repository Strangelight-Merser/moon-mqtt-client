///|
/// Minimal publish/subscribe CLI built on this library's public API.
///
/// It exists so another person can try the client and reproduce the failure
/// behaviour without writing MoonBit:
///
///   mqtt-cli publish   --host 127.0.0.1 --port 1883 -t lab/state -m ON --qos 1 --retain
///   mqtt-cli subscribe --host 127.0.0.1 --port 1883 -t 'lab/#' --count 3 --stats
///
/// Credentials come from `MQTT_DEMO_USERNAME`/`MQTT_DEMO_PASSWORD` and are never
/// printed. Every result line distinguishes success, not-sent and unknown.
priv struct Options {
  host : String
  port : Int
  client_id : String
  tls : Bool
  ca_file : String?
  topic : String
  payload : String
  qos : @mqtt.QoS
  retain : Bool
  count : Int
  timeout_ms : Int
  show_stats : Bool
  verbose : Bool
}

///|
fn usage() -> String {
  "usage: mqtt-cli publish   -t <topic> -m <payload> [--qos 0|1] [--retain]\n" +
  "       mqtt-cli subscribe -t <filter> [--count N] [--timeout-ms N]\n" +
  "options: --host H --port N --client-id ID --tls --ca FILE --stats --verbose\n" +
  "credentials: MQTT_DEMO_USERNAME / MQTT_DEMO_PASSWORD environment variables"
}

///|
/// Parse the argument list. Unknown options fail loudly instead of being
/// ignored, so a typo cannot silently change what is published.
fn parse_args(args : ArrayView[String]) -> Options raise {
  let mut host = "127.0.0.1"
  let mut port = 1883
  let mut client_id = "moon-mqtt-cli"
  let mut tls = false
  let mut ca_file : String? = None
  let mut topic = ""
  let mut payload = ""
  let mut qos = @mqtt.AtMostOnce
  let mut retain = false
  let mut count = 1
  let mut timeout_ms = 10000
  let mut show_stats = false
  let mut verbose = false
  let mut index = 0
  while index < args.length() {
    let flag = args[index]
    let next = fn() raise {
      index += 1
      if index >= args.length() {
        fail("missing value for \{flag}")
      }
      args[index]
    }
    match flag {
      "--host" => host = next()
      "--port" => port = @string.from_str(next())
      "--client-id" => client_id = next()
      "--tls" => tls = true
      "--ca" => ca_file = Some(next())
      "-t" | "--topic" => topic = next()
      "-m" | "--payload" => payload = next()
      "--qos" =>
        qos = match next() {
          "0" => @mqtt.AtMostOnce
          "1" => @mqtt.AtLeastOnce
          other => fail("unsupported --qos \{other}; only 0 and 1 exist")
        }
      "--retain" => retain = true
      "--count" => count = @string.from_str(next())
      "--timeout-ms" => timeout_ms = @string.from_str(next())
      "--stats" => show_stats = true
      "--verbose" => verbose = true
      "-h" | "--help" => fail(usage())
      other => fail("unknown option \{other}\n\{usage()}")
    }
    index += 1
  }
  if topic.is_empty() {
    fail("a topic is required\n\{usage()}")
  }
  {
    host,
    port,
    client_id,
    tls,
    ca_file,
    topic,
    payload,
    qos,
    retain,
    count,
    timeout_ms,
    show_stats,
    verbose,
  }
}

///|
fn Options::mqtt_config(self : Options) -> @mqtt.Config {
  @mqtt.Config::new(
    self.host,
    self.client_id,
    port=self.port,
    tls=if self.tls {
      match self.ca_file {
        Some(path) => @mqtt.CustomCA(path)
        None => @mqtt.SystemRoots
      }
    } else {
      @mqtt.Plain
    },
    username=@env.get_env_var("MQTT_DEMO_USERNAME"),
    password=match @env.get_env_var("MQTT_DEMO_PASSWORD") {
      Some(text) => Some(@utf8.encode(text))
      None => None
    },
    operation_timeout_ms=5000,
    write_timeout_ms=5000,
    ping_response_timeout_ms=5000,
  )
}

///|
fn json_string(value : String) -> String {
  @contracts.json_encode_string(value)
}

///|
/// Terminal, user-visible CLI failure. The message is printed by the process
/// runtime and the exit status is non-zero, which the demo scripts assert on.
suberror CliFailure {
  CliFailure(String)
}

///|
fn qos_text(qos : @mqtt.QoS) -> String {
  match qos {
    @mqtt.AtMostOnce => "0"
    @mqtt.AtLeastOnce => "1"
  }
}

///|
async fn snapshot(stats : @mqtt.ClientStats) -> Unit {
  @demo.log_line(
    "{\"event\":\"stats\",\"generation\":\{stats.generation},\"connected\":\{stats.connected},\"business_queue\":\{stats.business_queue_depth},\"control_queue\":\{stats.control_queue_depth},\"event_queue\":\{stats.event_queue_depth},\"pending\":\{stats.pending_requests},\"reconnects\":\{stats.reconnects},\"disconnects\":\{stats.disconnects},\"unknown\":\{stats.unknown_outcomes},\"last_disconnect\":\{match stats.last_disconnect { Some(reason) => json_string(reason) ; None => "null" }}}",
  )
}

///|
async fn publish_mode(options : Options) -> Unit {
  @mqtt.with_client(options.mqtt_config(), async fn(client) {
    if options.verbose {
      @demo.log_line(
        "{\"event\":\"connected\",\"generation\":\{client.stats().generation}}",
      )
    }
    try {
      client.publish(
        options.topic,
        @utf8.encode(options.payload),
        qos=options.qos,
        retain=options.retain,
      )
      @demo.log_line(
        "{\"event\":\"publish\",\"outcome\":\"sent\",\"topic\":\{json_string(options.topic)},\"qos\":\"\{qos_text(options.qos)}\",\"retain\":\{options.retain}}",
      )
    } catch {
      @mqtt.NotSent(reason) => {
        @demo.log_line(
          "{\"event\":\"publish\",\"outcome\":\"not_sent\",\"reason\":\{json_string(reason)}}",
        )
        raise CliFailure("publish was not sent: " + reason)
      }
      @mqtt.OutcomeUnknown(reason) => {
        @demo.log_line(
          "{\"event\":\"publish\",\"outcome\":\"unknown\",\"reason\":\{json_string(reason)}}",
        )
        raise CliFailure("publish outcome is unknown: " + reason)
      }
      error => {
        @demo.log_line(
          "{\"event\":\"publish\",\"outcome\":\"error\",\"reason\":\{json_string(error.to_string())}}",
        )
        raise error
      }
    }
    if options.show_stats {
      snapshot(client.stats())
    }
    client.disconnect()
  })
}

///|
async fn subscribe_mode(options : Options) -> Unit {
  @mqtt.with_client(options.mqtt_config(), async fn(client) {
    let granted = client.subscribe([
      { topic: options.topic, qos: options.qos },
    ])
    for result in granted {
      if result is @mqtt.Rejected {
        @demo.log_line(
          "{\"event\":\"subscribe\",\"outcome\":\"rejected\",\"topic\":\{json_string(options.topic)}}",
        )
        raise CliFailure("the broker rejected the subscription")
      }
    }
    @demo.log_line(
      "{\"event\":\"subscribed\",\"topic\":\{json_string(options.topic)},\"qos\":\"\{qos_text(options.qos)}\"}",
    )
    let mut received = 0
    while received < options.count {
      let event = @async.with_timeout_opt(options.timeout_ms, () => client.next_event())
      match event {
        None => {
          @demo.log_line(
            "{\"event\":\"timeout\",\"received\":\{received}}",
          )
          break
        }
        Some(@mqtt.MessageReceived(message)) => {
          received += 1
          @demo.log_line(
            "{\"event\":\"message\",\"topic\":\{json_string(message.topic)},\"payload\":\{json_string(@utf8.decode(message.payload) catch { _ => "<binary>" })},\"qos\":\"\{qos_text(message.qos)}\",\"retain\":\{message.retain},\"generation\":\{message.generation}}",
          )
        }
        Some(@mqtt.Disconnected(generation, reason)) =>
          @demo.log_line(
            "{\"event\":\"disconnected\",\"generation\":\{generation},\"reason\":\{json_string(reason)}}",
          )
        Some(@mqtt.Connected(generation)) =>
          if options.verbose {
            @demo.log_line(
              "{\"event\":\"connected\",\"generation\":\{generation}}",
            )
          }
      }
    }
    if options.show_stats {
      snapshot(client.stats())
    }
    client.disconnect()
  })
}

///|
async fn main {
  let args = @env.args()
  let unmasked = match args {
    [_, mode, ..rest] => (mode, rest)
    _ => {
      fail(usage())
    }
  }
  let (mode, rest) = unmasked
  let options = parse_args(rest[:])
  match mode {
    "publish" => publish_mode(options)
    "subscribe" | "sub" => subscribe_mode(options)
    other => fail("unknown mode \{other}\n\{usage()}")
  }
}
