///|
/// A closeable byte stream transferred to the internal WebSocket engine.
priv struct WebSocketStream {
  reader : &@io.Reader
  writer : &@io.Writer
  close_transport : () -> Unit
}

///|
#warnings("-alert_internal")
impl @io.Reader for WebSocketStream with fn _get_internal_buffer(self) {
  self.reader._get_internal_buffer()
}

///|
impl @io.Reader for WebSocketStream with fn _direct_read(
  self,
  buf,
  offset~,
  max_len~,
) {
  self.reader._direct_read(buf, offset~, max_len~)
}

///|
impl @io.Writer for WebSocketStream with fn write_once(
  self,
  buf,
  offset~,
  len~,
) {
  self.writer.write_once(buf, offset~, len~)
}

///|
impl @ws.Transport for WebSocketStream with fn close(self) {
  (self.close_transport)()
}

///|
/// Present successive WebSocket binary messages as the continuous byte stream
/// required by MQTT 3.1.1. MQTT packets may span WebSocket messages and a
/// message may contain more than one packet.
#warnings("-alert_internal")
priv struct WebSocketReader {
  conn : @ws.Conn
  mut message : @ws.Message?
  read_buf : @io.ReaderBuffer
}

///|
#warnings("-alert_internal")
fn WebSocketReader::new(conn : @ws.Conn) -> WebSocketReader {
  { conn, message: None, read_buf: @io.ReaderBuffer::new(), }
}

///|
#warnings("-alert_internal")
impl @io.Reader for WebSocketReader with fn _get_internal_buffer(self) {
  self.read_buf
}

///|
impl @io.Reader for WebSocketReader with fn _direct_read(
  self,
  buf,
  offset~,
  max_len~,
) {
  while true {
    match self.message {
      Some(message) => {
        let n = message.read(buf, offset~, max_len~) catch {
          @ws.ProtocolError(reason) =>
            raise ProtocolError("WebSocket protocol error: \{reason}")
          error => raise error
        }
        if n > 0 {
          return n
        }
        self.message = None
      }
      None => {
        let message = self.conn.recv() catch {
          @ws.ProtocolError(reason) =>
            raise ProtocolError("WebSocket protocol error: \{reason}")
          error => raise error
        }
        match message.kind {
          @ws.Binary => self.message = Some(message)
        }
      }
    }
  }
  panic()
}

///|
/// One MQTT transport write becomes one binary WebSocket message. The upstream
/// engine may fragment that message into bounded frames without changing MQTT
/// packet bytes or the caller's write-completion point.
priv struct WebSocketWriter {
  conn : @ws.Conn
}

///|
impl @io.Writer for WebSocketWriter with fn write_once(
  self,
  buf,
  offset~,
  len~,
) {
  let bytes = buf[offset:offset + len]
  self.conn.send_binary(bytes) catch {
    @ws.ProtocolError(reason) =>
      raise ProtocolError("WebSocket protocol error: \{reason}")
    error => raise error
  }
  len
}

///|
fn websocket_host_header(host : String, port : Int) -> String {
  let authority = if host.contains(":") { "[\{host}]" } else { host }
  "\{authority}:\{port}"
}
