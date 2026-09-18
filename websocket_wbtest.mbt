///|
fn config_rejected(config : Config) -> Bool {
  try {
    config.validate()
    false
  } catch {
    InvalidConfig(_) => true
    _ => false
  }
}

///|
test "network transport defaults to TCP and accepts a safe WebSocket path" {
  let tcp = Config::new("broker.example", "tcp")
  assert_true(tcp.transport is Tcp)
  let websocket = Config::new(
    "broker.example",
    "ws",
    transport=WebSocket("/mqtt?tenant=lab"),
  )
  websocket.validate()
  assert_true(websocket.transport is WebSocket("/mqtt?tenant=lab"))
}

///|
test "WebSocket authority and request target reject injection and unbounded input" {
  let bad_hosts = [
    "broker.example\\r\\nX-Test: injected",
    "broker.example/path",
    "用户.example",
    "a".repeat(1025),
  ]
  for host in bad_hosts {
    assert_true(
      config_rejected(
        Config::new(host, "bad-host", transport=WebSocket("/mqtt")),
      ),
    )
  }
  let bad_paths = [
    "mqtt",
    "/mqtt\\r\\nX-Test: injected",
    "/mqtt path",
    "/mqtt#fragment",
    "/你好",
    "/" + "a".repeat(8192),
  ]
  for path in bad_paths {
    assert_true(
      config_rejected(
        Config::new("broker.example", "bad-path", transport=WebSocket(path)),
      ),
    )
  }
}

///|
test "WebSocket Host header includes port and brackets IPv6 literals" {
  assert_eq(websocket_host_header("broker.example", 8083), "broker.example:8083")
  assert_eq(websocket_host_header("::1", 8083), "[::1]:8083")
}

///|
test "WSS transport closure is classified as a TLS failure" {
  match websocket_handshake_error(@io.ReaderClosed, true) {
    TlsFailure(reason) => assert_true(reason.contains("WebSocket handshake"))
    _ => abort("expected TLS failure")
  }
  assert_true(websocket_handshake_error(@io.ReaderClosed, false) is @io.ReaderClosed)
}

///|
test "TCP host validation remains unchanged by HTTP authority rules" {
  let config = Config::new("用户.example", "tcp-non-ascii")
  config.validate()
}
