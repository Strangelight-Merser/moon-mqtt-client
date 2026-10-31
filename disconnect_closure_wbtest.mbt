///|
fn closure_disconnect(session : Session) -> (Pending, Outgoing) {
  let request = test_pending(0, Disconnect, queued_at=@async.now())
  session.pending[0] = request
  (request, { bytes: b"\xe0\x00", pending: Some(request), delivery: None, is_ping: false })
}

///|
async test "closure DISCONNECT aborted before admission never enters writer" {
  for _ in 0..<3 {
    let writer = HeartbeatGateWriter::new()
    let session = heartbeat_test_session(writer).0
    let (request, item) = closure_disconnect(session)
    session.abort("peer closed before write")
    ignore(session.write_one(item, 1000, 1000, 2))
    assert_eq(writer.writes, 0)
    assert_false(request.started)
    assert_false(request.local_write_complete)
    match request.result {
      Some(Err(NotSent(_))) => ()
      _ => abort("pre-admission close must remain NotSent")
    }
  }
}

///|
async test "closure DISCONNECT held writer cannot replace an earlier terminal result" {
  for _ in 0..<3 {
    let writer = HeartbeatGateWriter::new()
    let session = heartbeat_test_session(writer).0
    let (request, item) = closure_disconnect(session)
    @async.with_task_group(group => {
      let writing = group.spawn(() => session.write_one(item, 1000, 1000, 2))
      writer.wait_entered()
      assert_true(request.started)
      assert_false(request.local_write_complete)
      session.abort("reader closed while writer was held")
      writer.release()
      assert_false(writing.wait())
    })
    assert_true(request.local_write_complete)
    match request.result {
      Some(Err(OutcomeUnknown("reader closed while writer was held"))) => ()
      _ => abort("late writer return replaced first terminal result")
    }
    assert_eq(session.pending.length(), 0)
  }
}

///|
async test "closure DISCONNECT deadline actually fires inside held writer" {
  for _ in 0..<3 {
    let writer = HeartbeatGateWriter::new()
    let session = heartbeat_test_session(writer).0
    let (request, item) = closure_disconnect(session)
    @async.with_task_group(group => {
      let writing = group.spawn(() => session.write_one(item, 1000, 20, 2))
      writer.wait_entered()
      assert_false(writing.wait())
    })
    assert_false(request.local_write_complete)
    match session.failure_reason {
      Some(WriteTimeout(_)) => ()
      _ => abort("actual timeout branch was not reached")
    }
    match request.result {
      Some(Err(OutcomeUnknown(_))) => ()
      _ => abort("started timed-out DISCONNECT must remain unknown")
    }
    assert_eq(session.pending.length(), 0)
  }
}

///|
async test "closure DISCONNECT scope cancellation joins a held writer without success" {
  for _ in 0..<3 {
    let writer = HeartbeatGateWriter::new()
    let session = heartbeat_test_session(writer).0
    let (request, item) = closure_disconnect(session)
    let mut returned_normally = false
    @async.with_task_group(group => {
      group.spawn_bg(() => {
        ignore(session.write_one(item, 1000, 1000, 2))
        returned_normally = true
      })
      writer.wait_entered()
      group.return_immediately(())
    })
    session.abort("scope cancelled")
    assert_false(returned_normally)
    assert_true(request.started)
    assert_false(request.local_write_complete)
    match request.result {
      Some(Err(OutcomeUnknown(_))) => ()
      _ => abort("cancelled unfinished write must not become successful")
    }
    assert_eq(session.pending.length(), 0)
  }
}
