///|
test "server disconnect retry is explicit opt in" {
  let defaults = Config::new("localhost", "default-policy", protocol=Mqtt5)
  assert_eq(defaults.server_disconnect_policy, StopOnServerDisconnect)

  let opted_in = Config::new(
    "localhost",
    "retry-policy",
    protocol=Mqtt5,
    server_disconnect_policy=RetryServerBusyOrShutdown,
  )
  assert_eq(
    opted_in.server_disconnect_policy,
    RetryServerBusyOrShutdown,
  )
  opted_in.validate()
}

///|
test "server retry timing arithmetic is bounded at Int maximum" {
  assert_eq(server_retry_wait_ms(0x7FFFFFFF, 999), 0x7FFFFFFF)
  assert_eq(
    grow_server_retry_delay(0x7FFFFFFE, 0x7FFFFFFF),
    0x7FFFFFFF,
  )
  assert_eq(grow_server_retry_delay(4, 10), 6)
  assert_eq(server_retry_wait_ms(4, 3), 7)
}
