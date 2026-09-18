// Derived-package white-box tests for MQTT handshake requirements.

///|
fn valid_upgrade(extra? : String = "") -> String {
  "HTTP/1.1 101 Switching Protocols\r\n" +
  "Upgrade: websocket\r\n" +
  "Connection: keep-alive, Upgrade\r\n" +
  "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n" +
  "Sec-WebSocket-Protocol: mqtt\r\n" +
  extra +
  "\r\n"
}

///|
test "handshake requires exact mqtt subprotocol" {
  validate_handshake(
    valid_upgrade(),
    "dGhlIHNhbXBsZSBub25jZQ==",
  )
  let missing = valid_upgrade().replace_all(
    old="Sec-WebSocket-Protocol: mqtt\r\n",
    new="",
  )
  let wrong = valid_upgrade().replace_all(
    old="Sec-WebSocket-Protocol: mqtt",
    new="Sec-WebSocket-Protocol: MQTT",
  )
  for response in [missing, wrong] {
    let rejected = try {
      validate_handshake(response, "dGhlIHNhbXBsZSBub25jZQ==")
      false
    } catch {
      InvalidHandshake(_) => true
      _ => false
    }
    assert_true(rejected)
  }
}

///|
test "handshake rejects unoffered extensions and token substrings" {
  let extension = valid_upgrade(
    extra="Sec-WebSocket-Extensions: permessage-deflate\r\n",
  )
  let bad_connection = valid_upgrade().replace_all(
    old="Connection: keep-alive, Upgrade",
    new="Connection: xupgrade",
  )
  for response in [extension, bad_connection] {
    let rejected = try {
      validate_handshake(response, "dGhlIHNhbXBsZSBub25jZQ==")
      false
    } catch {
      InvalidHandshake(_) => true
      _ => false
    }
    assert_true(rejected)
  }
}
