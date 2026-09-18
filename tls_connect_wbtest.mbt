///|
struct ConnectResetWriter {}

///|
impl @io.Writer for ConnectResetWriter with fn write_once(
  _self,
  _buf,
  offset~,
  len~,
) {
  ignore(offset)
  ignore(len)
  raise @os_error.OSError(54, context="simulated TLS transport reset")
}

///|
struct ConnectTimeoutWriter {}

///|
impl @io.Writer for ConnectTimeoutWriter with fn write_once(
  _self,
  _buf,
  offset~,
  len~,
) {
  @async.with_timeout(1, () => @async.sleep(20))
  offset + len
}

///|
struct ConnectSinkWriter {}

///|
impl @io.Writer for ConnectSinkWriter with fn write_once(
  _self,
  _buf,
  offset~,
  len~,
) {
  offset + len
}

///|
async test "TLS CONNECT maps a raw socket reset to TlsFailure" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\x20\x02\x00\x00")
  })
  defer reader.close()
  try {
    ignore(
      mqtt_connect_exchange(
        reader,
        ConnectResetWriter::{  },
        b"connect",
        1024,
        true,
      ),
    )
    abort("TLS socket reset must fail")
  } catch {
    TlsFailure(reason) =>
      assert_true(reason.contains("simulated TLS transport reset"))
    _ => abort("expected TlsFailure for TLS socket reset")
  }
}

///|
async test "plain CONNECT preserves a raw socket reset" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\x20\x02\x00\x00")
  })
  defer reader.close()
  try {
    ignore(
      mqtt_connect_exchange(
        reader,
        ConnectResetWriter::{  },
        b"connect",
        1024,
        false,
      ),
    )
    abort("plain socket reset must fail")
  } catch {
    @os_error.OSError(_) => ()
    _ => abort("plain transport error changed type")
  }
}

///|
async test "TLS CONNECT preserves timeout classification" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\x20\x02\x00\x00")
  })
  defer reader.close()
  try {
    ignore(
      mqtt_connect_exchange(
        reader,
        ConnectTimeoutWriter::{  },
        b"connect",
        1024,
        true,
      ),
    )
    abort("timeout must fail")
  } catch {
    @async.TimeoutError => ()
    _ => abort("TLS CONNECT timeout changed type")
  }
}

///|
async test "TLS CONNECT keeps malformed MQTT as ProtocolError" {
  let reader = @io.MemoryReader(async fn(writer) {
    writer.write(b"\x30\xff\xff\xff\x80")
  })
  defer reader.close()
  try {
    ignore(
      mqtt_connect_exchange(
        reader,
        ConnectSinkWriter::{  },
        b"connect",
        268435455,
        true,
      ),
    )
    abort("malformed MQTT must fail")
  } catch {
    ProtocolError(reason) => assert_eq(reason, "malformed remaining length")
    _ => abort("malformed MQTT changed type")
  }
}
