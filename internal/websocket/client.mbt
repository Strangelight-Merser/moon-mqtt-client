// Copyright 2025 International Digital Economy Academy
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.


///|
const MAX_HANDSHAKE_BYTES = 16384

///|
fn header_has_token(value : String, expected : String) -> Bool {
  value
  .split(",")
  .any(part => part.trim().to_lower() == expected)
}

///|
fn validate_handshake(response : String, key : String) -> Unit raise {
  let lines = response.split("\r\n").to_array()
  guard lines.length() > 0 else {
    raise InvalidHandshake("empty HTTP upgrade response")
  }
  let status = lines[0].split(" ").to_array()
  guard status.length() >= 2 &&
    status[0] == "HTTP/1.1" &&
    status[1] == "101" else {
    raise InvalidHandshake("server did not return HTTP/1.1 101")
  }
  let headers : Map[String, String] = Map([])
  for i in 1..<lines.length() {
    let line = lines[i]
    if line == "" {
      continue
    }
    guard line.split_once(":") is Some((name, value)) else {
      raise InvalidHandshake("malformed HTTP upgrade header")
    }
    let name = name.trim().to_lower().to_owned()
    guard name != "" else {
      raise InvalidHandshake("empty HTTP upgrade header name")
    }
    let value = value.trim().to_owned()
    headers[name] = match headers.get(name) {
      Some(previous) => previous + "," + value
      None => value
    }
  }
  guard headers.get("upgrade") is Some(upgrade) &&
    upgrade.to_lower() == "websocket" else {
    raise InvalidHandshake("missing or invalid Upgrade header")
  }
  guard headers.get("connection") is Some(connection) &&
    header_has_token(connection, "upgrade") else {
    raise InvalidHandshake("missing or invalid Connection header")
  }
  guard headers.get("sec-websocket-accept") is Some(accept) &&
    accept == generate_accept_key(key) else {
    raise InvalidHandshake("missing or invalid Sec-WebSocket-Accept header")
  }
  guard headers.get("sec-websocket-protocol") is Some(protocol) &&
    protocol == "mqtt" else {
    raise InvalidHandshake("server did not select the mqtt subprotocol")
  }
  guard headers.get("sec-websocket-extensions") is None else {
    raise InvalidHandshake("server selected an extension that was not offered")
  }
}

///|
async fn read_handshake(transport : &Transport) -> String raise {
  let bytes : Array[Byte] = []
  let mut matched = 0
  while bytes.length() < MAX_HANDSHAKE_BYTES {
    let byte = transport.read_exactly(1)[0]
    bytes.push(byte)
    matched = match (matched, byte) {
      (0 | 2, b'\r') => matched + 1
      (1 | 3, b'\n') => matched + 1
      (_, b'\r') => 1
      _ => 0
    }
    if matched == 4 {
      return @utf8.decode(Bytes::from_array(bytes)) catch {
        _ => raise InvalidHandshake("HTTP upgrade response is not valid UTF-8")
      }
    }
  }
  raise InvalidHandshake("HTTP upgrade headers exceed 16384 bytes")
}

///|
/// Upgrade an already connected native stream to a client WebSocket carrying
/// MQTT. Ownership of `transport` is transferred to the returned connection.
#warnings("-alert_internal")
pub async fn Conn::connect(
  transport : &Transport,
  host_header : String,
  path : String,
) -> Conn {
  errdefer transport.close()
  let seed = @tls.rand_bytes(32)
  let rand = @random.Rand::chacha8(seed~)
  let nonce = FixedArray::make(16, b'\x00')
  nonce.unsafe_write_uint32_le(0, rand.uint())
  nonce.unsafe_write_uint32_le(4, rand.uint())
  nonce.unsafe_write_uint32_le(8, rand.uint())
  nonce.unsafe_write_uint32_le(12, rand.uint())
  let key = @base64.encode(nonce.unsafe_reinterpret_as_bytes())
  let request =
    "GET \{path} HTTP/1.1\r\n" +
    "Host: \{host_header}\r\n" +
    "Upgrade: websocket\r\n" +
    "Connection: Upgrade\r\n" +
    "Sec-WebSocket-Key: \{key}\r\n" +
    "Sec-WebSocket-Version: 13\r\n" +
    "Sec-WebSocket-Protocol: mqtt\r\n\r\n"
  transport.write(@utf8.encode(request))
  let response = read_handshake(transport)
  validate_handshake(response, key)
  Conn::new(transport, 4096, mask=Some(rand))
}
