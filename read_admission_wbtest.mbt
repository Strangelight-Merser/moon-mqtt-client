///|
// The pinned async runtime queues with_timeout_opt's body in a child coroutine.
// Queue the closer immediately after the reader: it runs after the outer alive
// check and before the timeout child touches the transport. No timer or sleep
// is needed to expose the admission race.
async test "read byte refuses a transport closed before timeout child starts" {
  let (reader, writer) = @io.pipe()
  let session = fixture_session(reader, writer, () => {
    reader.close()
    writer.close()
  })
  let mut entered = false
  @async.with_task_group(group => {
    let reading = group.spawn(() => {
      entered = true
      read_session_byte(session)
    })
    group.spawn_bg(() => {
      assert_true(entered)
      session.abort_with(ProtocolError("closed before read admission"))
    })
    assert_true(reading.wait() is None)
  })
  assert_false(session.alive)
}

///|
async test "read chunk refuses a transport closed before timeout child starts" {
  let (reader, writer) = @io.pipe()
  let session = fixture_session(reader, writer, () => {
    reader.close()
    writer.close()
  })
  let mut entered = false
  @async.with_task_group(group => {
    let reading = group.spawn(() => {
      entered = true
      read_session_chunk(session, 8)
    })
    group.spawn_bg(() => {
      assert_true(entered)
      session.abort_with(ProtocolError("closed before read admission"))
    })
    assert_true(reading.wait() is None)
  })
  assert_false(session.alive)
}

///|
async test "read chunk still reports peer EOF while the session is alive" {
  let (reader, writer) = @io.pipe()
  let session = fixture_session(reader, writer, () => {
    reader.close()
    writer.close()
  })
  defer session.abort("test complete")
  writer.close()
  try {
    ignore(read_session_chunk(session, 8))
    abort("live peer EOF must remain ReaderClosed")
  } catch {
    @io.ReaderClosed => ()
    error => raise error
  }
}
