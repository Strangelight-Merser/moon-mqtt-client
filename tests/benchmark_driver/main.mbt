///|
extern "C" fn now_ns() -> Int64 = "moon_bench_now_ns"

///|
extern "C" fn trace_enable() -> Int = "moon_bench_trace_enable"

///|
extern "C" fn trace_close() -> Unit = "moon_bench_trace_close"

///|
fn setting(name : String, default_value : String) -> String {
  @env.get_env_var("MQTT_BENCH_" + name).unwrap_or(default_value)
}

///|
fn number(name : String, default_value : String) -> Int raise {
  @string.from_str(setting(name, default_value))
}

///|
let log_gate : @semaphore.Semaphore = @semaphore.Semaphore(1)

///|
async fn log(line : String) -> Unit {
  log_gate.acquire()
  defer log_gate.release()
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}

///|
async fn barrier(event : String) -> Unit {
  log("{\"event\":\"\{event}\",\"ns\":\{now_ns()}}")
  ignore(@stdio.stdin.read_exactly(1))
}

///|
async fn workload(client : @mqtt.Client, phase : String, seconds : Int) -> Unit {
  let kind = setting("KIND", "publish")
  let payload = Bytes::make(number("PAYLOAD", "32"), b'X')
  let qos = if number("QOS", "1") == 0 {
    @mqtt.AtMostOnce
  } else {
    @mqtt.AtLeastOnce
  }
  let workers = number("WORKERS", "1")
  let start = now_ns()
  let end = start + seconds.to_int64() * 1000000000L
  log("{\"event\":\"phase_start\",\"phase\":\"\{phase}\",\"ns\":\{start}}")
  @async.with_task_group(group => {
    for worker in 0..<workers {
      group.spawn_bg(async fn() {
        let mut seq = 0
        let histogram = Array::make(10001, 0)
        while now_ns() < end {
          seq += 1
          let id = "bench-\{phase}-\{worker}-\{seq}"
          let began = now_ns()
          if kind == "durable" || kind == "stages" {
            let handle = if kind == "durable" {
              client.submit_durable_delivery(
                id,
                id,
                payload,
                @env.now().reinterpret_as_int64() + 60000L,
              )
            } else {
              client.submit_delivery(id, id, payload)
            }
            let admission = now_ns()
            let status = handle.wait()
            if status.phase != @mqtt.Acknowledged {
              fail("measurement delivery was not acknowledged")
            }
            let completed = now_ns()
            log(
              "{\"event\":\"stages\",\"id\":\"\{id}\",\"begin_ns\":\{began},\"admitted_ns\":\{admission},\"completed_ns\":\{completed}}",
            )
          } else {
            client.publish(id, payload, qos~)
          }
          let bucket = ((now_ns() - began) / 10000L)
            .to_int()
            .clamp(min=0, max=10000)
          histogram[bucket] += 1
        }
        for bucket in 0..<histogram.length() {
          if histogram[bucket] > 0 {
            log(
              "{\"event\":\"bucket\",\"phase\":\"\{phase}\",\"worker\":\{worker},\"upper_us\":\{(bucket + 1) * 10},\"count\":\{histogram[bucket]}}",
            )
          }
        }
        log(
          "{\"event\":\"outcomes\",\"phase\":\"\{phase}\",\"worker\":\{worker},\"first\":1,\"last\":\{seq},\"accepted\":\{seq},\"completed\":\{seq},\"rejected\":0,\"unknown\":0}",
        )
      })
    }
  })
  let stats = client.stats()
  log(
    "{\"event\":\"phase_end\",\"phase\":\"\{phase}\",\"ns\":\{now_ns()},\"pending\":\{stats.pending_requests},\"business\":\{stats.business_queue_depth}}",
  )
}

///|
async fn special_workload(
  client : @mqtt.Client,
  phase : String,
  seconds : Int,
) -> Unit {
  let kind = setting("KIND", "publish")
  let end = now_ns() + seconds.to_int64() * 1000000000L
  let mut sequence = 0
  log("{\"event\":\"phase_start\",\"phase\":\"\{phase}\",\"ns\":\{now_ns()}}")
  while now_ns() < end {
    sequence += 1
    let start = now_ns()
    if kind == "subscriptions" {
      let previous = client.stats().generation
      client.publish(
        "bench-reset-\{phase}-\{sequence}",
        b"reset",
        qos=@mqtt.AtLeastOnce,
      )
      let mut done = false
      while !done {
        match client.next_event() {
          @mqtt.Connected(generation) => done = generation > previous
          _ => ()
        }
      }
    } else {
      let first = client.submit_delivery(
        "bench-\{phase}-0-\{sequence}",
        "bench-flow-1",
        b"one",
      )
      let second = client.submit_delivery(
        "bench-\{phase}-1-\{sequence}",
        "bench-flow-2",
        b"two",
      )
      ignore(
        client.subscribe([
          { topic: "bench/nonpublish", qos: @mqtt.AtLeastOnce, },
        ]),
      )
      let acknowledged = now_ns()
      if first.wait().phase != @mqtt.Acknowledged ||
        second.wait().phase != @mqtt.Acknowledged {
        fail("flow measurement publish failed")
      }
      log(
        "{\"event\":\"nonpublish\",\"phase\":\"\{phase}\",\"sequence\":\{sequence},\"ns\":\{acknowledged - start}}",
      )
    }
    log(
      "{\"event\":\"cycle\",\"phase\":\"\{phase}\",\"sequence\":\{sequence},\"ns\":\{now_ns() - start}}",
    )
  }
  log(
    "{\"event\":\"phase_end\",\"phase\":\"\{phase}\",\"ns\":\{now_ns()},\"cycles\":\{sequence}}",
  )
}

///|
async fn run(client : @mqtt.Client) -> Unit {
  let kind = setting("KIND", "publish")
  if kind == "subscriptions" {
    ignore(
      client.subscribe(
        Array::makei(number("FILTERS", "100"), i => {
          topic: "bench/filter/\{i}",
          qos: @mqtt.AtLeastOnce,
        }),
      ),
    )
  }
  if kind == "flow" || kind == "subscriptions" {
    special_workload(client, "warmup", number("WARMUP", "5"))
    barrier("warmed")
    special_workload(client, "sample", number("SECONDS", "30"))
  } else {
    workload(client, "warmup", number("WARMUP", "5"))
    barrier("warmed")
    workload(client, "sample", number("SECONDS", "30"))
  }
}

///|
async fn main {
  let kind = setting("KIND", "publish")
  let cfg = @mqtt.Config::new(
    "127.0.0.1",
    "moon-benchmark",
    port=number("PORT", "1883"),
    session_policy=if kind == "publish" || kind == "subscriptions" {
      @mqtt.CleanSession
    } else {
      @mqtt.ResumeSession
    },
    protocol=if kind == "flow" { @mqtt.Mqtt5 } else { @mqtt.Mqtt311 },
    session_expiry_secs=if kind == "flow" { 60L } else { 0L },
    max_inflight=number("INFLIGHT", "32"),
    send_capacity=64,
    max_packet_size=65536,
    operation_timeout_ms=10000,
    write_timeout_ms=10000,
    reconnect_delay_ms=10,
    max_reconnect_delay_ms=20,
    reconnect_attempts=10,
  )
  if kind == "durable" {
    if trace_enable() != 0 {
      fail("cannot install isolated SQLite measurement trace")
    }
  }
  defer trace_close()
  barrier("before_scope")
  if kind == "durable" {
    @mqtt.with_durable_client(
      @mqtt.DurableOutboxOptions::new(setting("DB", "")),
      cfg,
      async fn(client, recovered) {
        if recovered.length() > 0 {
          fail("benchmark must use a fresh database")
        }
        run(client)
      },
    )
  } else {
    @mqtt.with_client(cfg, client => run(client))
  }
  barrier("scope_closed")
}
