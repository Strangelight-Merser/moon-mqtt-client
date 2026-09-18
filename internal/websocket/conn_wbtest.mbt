priv struct ScriptTransport {
  reader : @io.MemoryReader
  written : Array[Byte]
  mut closed : Bool
}

///|
fn scripted(data : Bytes) -> ScriptTransport {
  {
    reader: @io.MemoryReader(async fn(writer) { writer.write(data) }),
    written: [],
    closed: false,
  }
}

///|
#warnings("-alert_internal")
impl @io.Reader for ScriptTransport with fn _get_internal_buffer(self) {
  self.reader._get_internal_buffer()
}

///|
impl @io.Reader for ScriptTransport with fn _direct_read(
  self,
  buf,
  offset~,
  max_len~,
) {
  self.reader._direct_read(buf, offset~, max_len~)
}

///|
impl @io.Writer for ScriptTransport with fn write_once(
  self,
  buf,
  offset~,
  len~,
) {
  for byte in buf[offset:offset + len] {
    self.written.push(byte)
  }
  len
}

///|
impl Transport for ScriptTransport with fn close(self) {
  if !self.closed {
    self.closed = true
    self.reader.close()
  }
}

///|
priv struct BlockingWriteTransport {
  reader : @io.MemoryReader
  written : Array[Byte]
  started : @cond_var.Cond
  released : @cond_var.Cond
  mut first_write_started : Bool
  mut release_first : Bool
}

///|
fn blocking_writer() -> BlockingWriteTransport {
  {
    reader: scripted(b"").reader,
    written: [],
    started: @cond_var.Cond(),
    released: @cond_var.Cond(),
    first_write_started: false,
    release_first: false,
  }
}

///|
#warnings("-alert_internal")
impl @io.Reader for BlockingWriteTransport with fn _get_internal_buffer(self) {
  self.reader._get_internal_buffer()
}

///|
impl @io.Reader for BlockingWriteTransport with fn _direct_read(
  self,
  buf,
  offset~,
  max_len~,
) {
  self.reader._direct_read(buf, offset~, max_len~)
}

///|
impl @io.Writer for BlockingWriteTransport with fn write_once(
  self,
  buf,
  offset~,
  len~,
) {
  if !self.first_write_started {
    self.first_write_started = true
    self.started.broadcast()
    while !self.release_first {
      self.released.wait()
    }
  }
  for byte in buf[offset:offset + len] {
    self.written.push(byte)
  }
  len
}

///|
impl Transport for BlockingWriteTransport with fn close(self) {
  self.reader.close()
  self.release_first = true
  self.released.broadcast()
}

///|
priv suberror CloseWriteFailure

///|
priv struct FailingWriteTransport {
  reader : @io.MemoryReader
  mut writes : Int
  mut closed : Bool
}

///|
#warnings("-alert_internal")
impl @io.Reader for FailingWriteTransport with fn _get_internal_buffer(self) {
  self.reader._get_internal_buffer()
}

///|
impl @io.Reader for FailingWriteTransport with fn _direct_read(
  self,
  buf,
  offset~,
  max_len~,
) {
  self.reader._direct_read(buf, offset~, max_len~)
}

///|
impl @io.Writer for FailingWriteTransport with fn write_once(
  self,
  _buf,
  offset~,
  len~,
) {
  ignore(offset)
  ignore(len)
  self.writes += 1
  raise CloseWriteFailure
}

///|
impl Transport for FailingWriteTransport with fn close(self) {
  self.closed = true
  self.reader.close()
}

///|
async test "binary message streams across fragments and peer ping" {
  // Binary first fragment: c0; peer PING; final continuation: 00.
  let transport = scripted(b"\x02\x01\xc0\x89\x01x\x80\x01\x00")
  defer Transport::close(transport)
  let conn = Conn::new(transport, 4096, mask=None)
  let message = conn.recv()
  assert_true(message.kind is Binary)
  assert_eq(message.read_all().binary(), b"\xc0\x00")
  // The peer PING was consumed and answered with an unmasked PONG in this
  // server-style test connection. Production client connections are masked.
  assert_eq(Bytes::from_array(transport.written), b"\x8a\x01x")
}

///|
async test "masked server frame is rejected" {
  let transport = scripted(b"\x82\x82\x01\x02\x03\x04\xc1\x02")
  defer Transport::close(transport)
  let conn = Conn::new(transport, 4096, mask=None)
  let rejected = try {
    ignore(conn.recv())
    false
  } catch {
    ProtocolError(reason) => reason.contains("must not be masked")
    _ => false
  }
  assert_true(rejected)
}

///|
async test "framing violation fail-closes without attempting a response write" {
  let transport : FailingWriteTransport = {
    reader: scripted(b"\x82\x82\x01\x02\x03\x04\xc1\x02").reader,
    writes: 0,
    closed: false,
  }
  defer Transport::close(transport)
  let conn = Conn::new(transport, 4096, mask=None)
  let rejected = try {
    ignore(conn.recv())
    false
  } catch {
    ProtocolError(reason) => reason.contains("must not be masked")
    _ => false
  }
  assert_true(rejected)
  assert_eq(transport.writes, 0)
  assert_true(transport.closed)
}

///|
async test "close payload rejects one byte and reserved status codes" {
  for frame in [b"\x88\x01\x00", b"\x88\x02\x03\xed", b"\x88\x02\x07\xd0"] {
    let transport = scripted(frame)
    defer Transport::close(transport)
    let conn = Conn::new(transport, 4096, mask=None)
    let rejected = try {
      ignore(conn.recv())
      false
    } catch {
      ProtocolError(_) => true
      _ => false
    }
    assert_true(rejected)
  }
}

///|
async test "truncated data and control payloads preserve transport EOF" {
  let data_transport = scripted(b"\x82\x02x")
  defer Transport::close(data_transport)
  let data_conn = Conn::new(data_transport, 4096, mask=None)
  let data_eof = try {
    ignore(data_conn.recv().read_all())
    false
  } catch {
    @io.ReaderClosed => true
    _ => false
  }
  assert_true(data_eof)

  for frame in [b"\x89\x01", b"\x8a\x01", b"\x88\x02\x03"] {
    let transport = scripted(frame)
    defer Transport::close(transport)
    let conn = Conn::new(transport, 4096, mask=None)
    let control_eof = try {
      ignore(conn.recv())
      false
    } catch {
      @io.ReaderClosed => true
      _ => false
    }
    assert_true(control_eof)
  }
}

///|
async test "fragmented writer emits no data after peer close wins" {
  let transport = blocking_writer()
  defer Transport::close(transport)
  // With a 32-byte header reservation, a 36-byte frame buffer fragments this
  // eight-byte message after four payload bytes.
  let conn = Conn::new(transport, 36, mask=None)
  let mut sender_error : Error? = None
  @async.with_task_group(group => {
    let sender = group.spawn(() => {
      conn.send_binary(b"abcdefgh") catch {
        error => sender_error = Some(error)
      }
    })
    while !transport.first_write_started {
      transport.started.wait()
    }
    // Peer-close handling records the terminal state before waiting for the
    // write lock to emit its Close response. Model that exact interleaving
    // while the first data frame is inside the underlying write.
    conn.closed = Some(ConnectionClosed(Normal, None))
    transport.release_first = true
    transport.released.broadcast()
    sender.wait()
  })
  assert_true(sender_error is Some(ConnectionClosed(Normal, None)))
  // Only the in-progress first fragment may complete. A continuation after the
  // close state would violate RFC 6455's no-data-after-Close rule.
  assert_eq(Bytes::from_array(transport.written), b"\x02\x04abcd")
}
