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

///|
async test "WebSocket disconnect waits for peer closure after MQTT completion" {
  let config = Config::new(
    "localhost", "close-order", transport=WebSocket("/mqtt"),
    write_timeout_ms=200,
  )
  let client = cause_test_client(config)
  let session = cause_test_session(SlowCauseWriter::{})
  let peer_closed = [false]
  client.session = Some(session)
  @async.with_task_group(group => {
    group.spawn_bg(() => session.write_loop(500, 200, 0))
    group.spawn_bg(async fn() {
      while !session.pending.contains(0) {
        @async.sleep(1)
      }
      while session.pending.contains(0) {
        @async.sleep(1)
      }
      @async.sleep(5)
      peer_closed[0] = true
      client.abort_session("peer processed MQTT DISCONNECT")
    })
    client.disconnect()
    assert_true(peer_closed[0])
  })
  assert_true(!session.alive)
}

///|
async test "WebSocket disconnect bounds a silent peer and closes resources" {
  let config = Config::new(
    "localhost", "silent-close", transport=WebSocket("/mqtt"),
    write_timeout_ms=100,
  )
  let client = cause_test_client(config)
  let session = cause_test_session(SlowCauseWriter::{})
  client.session = Some(session)
  @async.with_task_group(group => {
    group.spawn_bg(() => session.write_loop(500, 100, 0))
    @async.with_timeout(500, () => client.disconnect())
  })
  assert_true(!session.alive)
}

///|
priv struct DisconnectOrderWriter {
  writes : Array[Bytes]
}

///|
impl @io.Writer for DisconnectOrderWriter with fn write_once(self, buf, offset~, len~) {
  self.writes.push(buf[offset:offset + len].to_owned())
  len
}

///|
async test "no queued MQTT packet is written after DISCONNECT" {
  let writer = DisconnectOrderWriter::{ writes: [] }
  let session = cause_test_session(writer)
  defer session.abort("test ended")
  let request = { ..test_pending(0, Disconnect), queued_at: @async.now() }
  session.pending[0] = request
  ignore(session.control_queue.try_put({
    bytes: b"\xe0\x00", pending: Some(request), is_ping: false, delivery: None,
  }))
  ignore(session.control_queue.try_put({
    bytes: b"\xc0\x00", pending: None, is_ping: true, delivery: None,
  }))
  session.control_depth = 2
  let ended = @async.with_timeout_opt(200, () => session.write_loop(500, 100, 0))
  assert_true(ended is Some(_))
  assert_eq(writer.writes.length(), 1)
  assert_eq(writer.writes[0], b"\xe0\x00")
  assert_true(request.result is Some(Ok(Done)))
}
