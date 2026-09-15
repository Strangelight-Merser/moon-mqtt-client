///|
struct Metrics {
  mut acknowledged : Int
  mut unknown : Int
  mut not_connected : Int
  mut connected_events : Int
  mut disconnected_events : Int
  mut active_workers : Int
  latency_ms : Array[Int]
}

///|
async fn log_line(line : String) -> Unit {
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
fn required_int(name : String) -> Int raise {
  @string.from_str(
    @env.get_env_var(name).unwrap_or_else(() => fail("missing " + name)),
  )
}

///|
fn config() -> @mqtt.Config raise {
  @mqtt.Config::new(
    "127.0.0.1",
    "moon-soak-driver",
    port=required_int("MQTT_SOAK_PORT"),
    operation_timeout_ms=5000,
    write_timeout_ms=5000,
    ping_response_timeout_ms=5000,
    send_capacity=64,
    receive_capacity=256,
    max_inflight=32,
    reconnect_delay_ms=50,
    max_reconnect_delay_ms=200,
    reconnect_attempts=1000,
  )
}

///|
async fn publish_worker(
  client : @mqtt.Client,
  worker : Int,
  deadline : Int64,
  payload : Bytes,
  metrics : Metrics,
) -> Unit {
  defer { metrics.active_workers -= 1 }
  while @async.now() < deadline {
    let started = @async.now()
    try {
      client.publish(
        "soak/qos1/\{worker}",
        payload,
        qos=@mqtt.AtLeastOnce,
      )
      let latency = (@async.now() - started).to_int().clamp(min=0, max=5000)
      metrics.acknowledged += 1
      metrics.latency_ms[latency] += 1
    } catch {
      @mqtt.OutcomeUnknown(_) => {
        metrics.unknown += 1
        if @async.now() < deadline {
          client.wait_connected()
        }
      }
      @mqtt.NotConnected => {
        metrics.not_connected += 1
        if @async.now() < deadline {
          client.wait_connected()
        }
      }
      @mqtt.NotSent(_) => {
        metrics.not_connected += 1
        if @async.now() < deadline {
          client.wait_connected()
        }
      }
      error => raise error
    }
  }
}

///|
async fn consume_events(
  client : @mqtt.Client,
  deadline : Int64,
  metrics : Metrics,
) -> Unit {
  while @async.now() < deadline || metrics.active_workers > 0 {
    match @async.with_timeout_opt(100, () => client.next_event()) {
      Some(@mqtt.Connected(generation)) => {
        metrics.connected_events += 1
        log_line(
          "{\"event\":\"connected\",\"generation\":\{generation},\"at_ms\":\{@async.now()}}",
        )
      }
      Some(@mqtt.Disconnected(generation, _)) => {
        metrics.disconnected_events += 1
        log_line(
          "{\"event\":\"disconnected\",\"generation\":\{generation},\"at_ms\":\{@async.now()}}",
        )
      }
      Some(@mqtt.MessageReceived(_)) => ()
      None => ()
    }
  }
}

///|
async fn main {
  let duration_seconds = required_int("MQTT_SOAK_DURATION_SECONDS")
  let concurrency = required_int("MQTT_SOAK_CONCURRENCY")
  let payload_bytes = required_int("MQTT_SOAK_PAYLOAD_BYTES")
  let payload = Bytes::makei(payload_bytes, index => (index % 251).to_byte())
  let metrics : Metrics = {
    acknowledged: 0,
    unknown: 0,
    not_connected: 0,
    connected_events: 0,
    disconnected_events: 0,
    active_workers: concurrency,
    latency_ms: Array::make(5001, 0),
  }
  @mqtt.with_client(config(), async fn(client) {
    client.set_reconnect_seed(0x5A17)
    let started = @async.now()
    let deadline = started + (duration_seconds * 1000).to_int64()
    log_line(
      "{\"event\":\"ready\",\"started_ms\":\{started},\"duration_seconds\":\{duration_seconds},\"concurrency\":\{concurrency},\"payload_bytes\":\{payload_bytes}}",
    )
    @async.with_task_group(group => {
      for worker in 0..<concurrency {
        group.spawn_bg(() => {
          publish_worker(client, worker, deadline, payload, metrics)
        })
      }
      group.spawn_bg(() => consume_events(client, deadline, metrics))
    })
    let drain_deadline = @async.now() + 5000L
    let mut snapshot = client.stats()
    while (snapshot.pending_requests > 0 ||
      snapshot.business_queue_depth > 0 || snapshot.control_queue_depth > 0) &&
      @async.now() < drain_deadline {
      @async.sleep(10)
      snapshot = client.stats()
    }
    for latency in 0..<metrics.latency_ms.length() {
      if metrics.latency_ms[latency] > 0 {
        log_line(
          "{\"event\":\"latency_bucket\",\"ms\":\{latency},\"count\":\{metrics.latency_ms[latency]}}",
        )
      }
    }
    log_line(
      "{\"event\":\"final\",\"ended_ms\":\{@async.now()},\"acknowledged\":\{metrics.acknowledged},\"unknown\":\{metrics.unknown},\"not_connected\":\{metrics.not_connected},\"active_workers\":\{metrics.active_workers},\"connected_events\":\{metrics.connected_events},\"disconnected_events\":\{metrics.disconnected_events},\"generation\":\{snapshot.generation},\"reconnects\":\{snapshot.reconnects},\"disconnects\":\{snapshot.disconnects},\"pending\":\{snapshot.pending_requests},\"business\":\{snapshot.business_queue_depth},\"control\":\{snapshot.control_queue_depth},\"event_queue\":\{snapshot.event_queue_depth}}",
    )
  })
}
