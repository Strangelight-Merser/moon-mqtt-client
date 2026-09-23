///|
async fn pause_at(event : String, iteration : Int) -> Unit {
  @stdio.stdout.write(@utf8.encode("{\"event\":\"\{event}\",\"iteration\":\{iteration}}\n"))
  ignore(@stdio.stdin.read_exactly(1))
}

///|
async fn main {
  let cfg = @mqtt.Config::new(
    "127.0.0.1",
    "task-census-scope",
    port=@string.parse_int(@env.get_env_var("CENSUS_PORT").unwrap()),
    keep_alive_secs=0,
    reconnect_delay_ms=50,
    max_reconnect_delay_ms=200,
    reconnect_attempts=1000,
  )
  pause_at("cold", 0)
  if @env.get_env_var("CENSUS_MODE") is Some("durable_reopen") ||
    @env.get_env_var("CENSUS_MODE") is Some("storage_failure") {
    let durable_cfg = @mqtt.Config::new(
      "127.0.0.1",
      "task-census-durable",
      port=@string.parse_int(@env.get_env_var("CENSUS_PORT").unwrap()),
      session_policy=@mqtt.ResumeSession,
      keep_alive_secs=0,
    )
    let options = @mqtt.DurableOutboxOptions::new(
      @env.get_env_var("CENSUS_OUTBOX").unwrap(),
      max_records=4,
      max_payload_bytes=65536L,
      max_pages=128,
    )
    if @env.get_env_var("CENSUS_MODE") is Some("storage_failure") {
      for iteration in 1..=20 {
        let mut failed = false
        try {
          ignore(@mqtt.inspect_durable_outbox(options, durable_cfg))
        } catch {
          @mqtt.DurableStoreCorrupt(_) => failed = true
          _ => ()
        }
        if !failed {
          fail("corrupt durable store did not produce DurableStoreCorrupt")
        }
        pause_at("storage_failure", iteration)
      }
    } else {
      for iteration in 1..=20 {
        @mqtt.with_durable_client(options, durable_cfg, fn(_client, _recovered) {
          ()
        })
        pause_at("durable_closed", iteration)
      }
    }
    return
  }
  if @env.get_env_var("CENSUS_MODE") is Some("timeout") {
    let timeout_cfg = @mqtt.Config::new(
      "127.0.0.1",
      "task-census-timeout",
      port=@string.parse_int(@env.get_env_var("CENSUS_PORT").unwrap()),
      operation_timeout_ms=100,
      reconnect_attempts=0,
      keep_alive_secs=0,
    )
    for iteration in 1..=20 {
      let mut observed_timeout = false
      try {
        @mqtt.with_client(timeout_cfg, async fn(client) {
          try {
            client.publish("census/timeout", b"x", qos=@mqtt.AtLeastOnce)
          } catch {
            @mqtt.OutcomeUnknown(_) => observed_timeout = true
            _ => ()
          }
        })
      } catch {
        _ => ()
      }
      if !observed_timeout {
        fail("native publish did not report timed-out started write")
      }
      pause_at("timeout_closed", iteration)
    }
    return
  }
  if @env.get_env_var("CENSUS_MODE") is Some("reconnect") {
    @mqtt.with_client(cfg, async fn(client) {
      let mut generation = client.stats().generation
      pause_at("ready", 0)
      for iteration in 1..=20 {
        while client.stats().generation <= generation {
          @async.sleep(10)
        }
        generation = client.stats().generation
        pause_at("reconnect_ready", iteration)
      }
    })
    pause_at("scope_closed", 20)
    return
  }
  let workers = @env.get_env_var("CENSUS_WORKERS")
  if workers is Some(worker_text) {
    let count = @string.parse_int(worker_text)
    let changed = @cond_var.Cond()
    let mut started = 0
    let mut released = false
    @async.with_task_group(group => {
      for _ in 0..<count {
        group.spawn_bg(() => {
          started += 1
          changed.broadcast()
          while !released {
            changed.wait()
          }
        })
      }
      while started < count {
        changed.wait()
      }
      pause_at("workers_open", count)
      released = true
      changed.broadcast()
    })
    pause_at("workers_closed", count)
    return
  }
  for iteration in 1..=20 {
    @mqtt.with_client(cfg, async fn(_client) {
      pause_at("scope_open", iteration)
    })
    pause_at("scope_closed", iteration)
  }
}
