///|
async test "idle TLS read cancellation preserves subsequent traffic" {
  @async.with_task_group(root => {
    let listener = listen()
    defer listener.close()
    root.spawn_bg(() => {
      let server = accept_server(listener, require_client=true)
      defer server.close()
      let bytes = server.tls.read_exactly(4)
      server.tls.write(bytes)
    })
    let client = connect_client(
      listener.addr.port(),
      identity=Some(client_id(cert="client.pem", key="client.key")),
      trust=testdata("server-ca.pem"),
    )
    defer client.close()
    let idle = @async.with_timeout_opt(30, () => client.tls.read_exactly(1))
    assert_true(idle is None)
    client.tls.write(b"ping")
    inspect(bytes_text(client.tls.read_exactly(4)), content="ping")
  })
}
