///|
async fn main {
  let port : Int = @string.from_str(@env.get_env_var("MQTT_TEST_PORT").unwrap())
  let config = @mqtt.Config::new(
    "localhost", "tls-timing", port~,
    tls=@mqtt.CustomCA(@env.get_env_var("MQTT_TEST_CA").unwrap()),
    client_identity=Some({
      certificate_chain_file: @env.get_env_var("MQTT_TEST_CERT").unwrap(),
      private_key_file: @env.get_env_var("MQTT_TEST_KEY").unwrap(),
    }),
  )
  let started = @async.now()
  for index in 0..<30 {
    let before = @async.now()
    @mqtt.with_client(config, async fn(client) {
      @stdio.stdout.write(@utf8.encode("iteration=\{index} connected_ms=\{@async.now()-before}\n"))
      client.publish("it/tls-timing", b"ok", qos=@mqtt.AtLeastOnce)
      @stdio.stdout.write(@utf8.encode("iteration=\{index} published_ms=\{@async.now()-before}\n"))
    })
    @stdio.stdout.write(@utf8.encode("iteration=\{index} closed_ms=\{@async.now()-before} total_ms=\{@async.now()-started}\n"))
  }
}
