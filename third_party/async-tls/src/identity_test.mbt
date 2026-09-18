// Copyright 2026 Strangelight-Merser
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
async fn testdata(name : String) -> String {
  let direct = "testdata/" + name
  if @fs.exists(direct) {
    direct
  } else {
    "src/testdata/" + name
  }
}

///|
async fn client_id(cert~ : String, key~ : String) -> ClientIdentity {
  {
    certificate_chain_file: testdata(cert),
    private_key_file: testdata(key),
  }
}

///|
fn bytes_text(data : Bytes) -> String raise {
  @utf8.decode(data)
}

///|
async fn listen() -> @socket.TcpServer {
  @socket.TcpServer::TcpServer(@socket.Addr::parse("127.0.0.1:0"))
}

///|
const TLS1_3_VERSION = 0x0304

///|
/// One test connection: the TLS wrapper plus the TCP socket underneath it.
///
/// `Tls` never owns or closes the transport it wraps, so every test scope has to
/// release the TCP handle explicitly (TLS first, transport second) to keep the
/// async event loop's FD-leak check clean on both the success and failure paths.
struct TestConnection {
  tls : Tls
  tcp : @socket.Tcp
}

///|
fn TestConnection::close(self : TestConnection) -> Unit {
  self.tls.close()
  self.tcp.close()
}

///|
async fn accept_server(
  listener : @socket.TcpServer,
  require_client~ : Bool,
  min_protocol_version? : Int,
) -> TestConnection {
  let (tcp, _) = listener.accept()
  errdefer tcp.close()
  let tls = Tls::server(
    tcp,
    private_key_file=testdata("server.key"),
    private_key_type=PEM,
    certificate_file=testdata("server.pem"),
    certificate_type=PEM,
    client_ca_file=if require_client {
      testdata("client-ca.pem")
    } else {
      testdata("server-ca.pem")
    },
    require_client_certificate=require_client,
    min_protocol_version?,
  )
  { tls, tcp }
}

///|
async fn connect_client(
  port : Int,
  identity~ : ClientIdentity?,
  trust~ : String,
) -> TestConnection {
  let tcp = @socket.Tcp::connect_to_host("127.0.0.1", port~)
  errdefer tcp.close()
  let tls = match identity {
    Some(id) =>
      Tls::client(
        tcp,
        host="localhost",
        trust=CustomPemFile(trust),
        identity=id,
      )
    None =>
      Tls::client(tcp, host="localhost", trust=CustomPemFile(trust))
  }
  { tls, tcp }
}

///|
async fn reject_identity(identity : ClientIdentity) -> String {
  try {
    ignore(
      Tls::client_from_pair(
        @io.pipe().0,
        @io.pipe().1,
        host="localhost",
        trust=CustomPemFile(testdata("server-ca.pem")),
        identity=identity,
      ),
    )
    "ok"
  } catch {
    err => describe_tls_error(err)
  }
}

///|
async test "encrypted private key is rejected without prompting" {
  inspect(
    reject_identity(client_id(cert="client.pem", key="client-encrypted.key")),
    content="client identity: encrypted private keys are not supported",
  )
}

///|
async test "mismatched private key is rejected" {
  let msg = reject_identity(client_id(cert="client.pem", key="client-mismatch.key"))
  assert_true(msg.contains("mismatch"))
}

///|
async test "missing private key file is rejected" {
  let msg = reject_identity({
    certificate_chain_file: testdata("client.pem"),
    private_key_file: testdata("no-such-key.pem"),
  })
  assert_true(msg.contains("failed to read private key file"))
}

///|
async test "tcp mtls handshake with client identity" {
  @async.with_task_group() <| root => {
    let listener = listen()
    defer listener.close()
    let port = listener.addr.port()
    root.spawn_bg() <| () => {
      let server = accept_server(listener, require_client=true)
      defer server.close()
      let data = server.tls.read_exactly(4)
      server.tls.write(data)
      try {
        server.tls.shutdown()
      } catch {
        _ => ()
      }
    }
    let client = connect_client(
      port,
      identity=Some(client_id(cert="client.pem", key="client.key")),
      trust=testdata("server-ca.pem"),
    )
    defer client.close()
    client.tls.write(b"abcd")
    inspect(bytes_text(client.tls.read_exactly(4)), content="abcd")
    client.tls.shutdown()
  }
}

///|
async test "tcp mtls accepts a client chain with intermediate" {
  @async.with_task_group() <| root => {
    let listener = listen()
    defer listener.close()
    let port = listener.addr.port()
    root.spawn_bg() <| () => {
      let server = accept_server(listener, require_client=true)
      defer server.close()
      let data = server.tls.read_exactly(5)
      server.tls.write(data)
      try {
        server.tls.shutdown()
      } catch {
        _ => ()
      }
    }
    let client = connect_client(
      port,
      identity=Some(
        client_id(cert="client-chain.pem", key="client-via-int.key"),
      ),
      trust=testdata("server-ca.pem"),
    )
    defer client.close()
    client.tls.write(b"chain")
    inspect(bytes_text(client.tls.read_exactly(5)), content="chain")
    client.tls.shutdown()
  }
}

///|
async test "tcp handshake without client identity" {
  @async.with_task_group() <| root => {
    let listener = listen()
    defer listener.close()
    let port = listener.addr.port()
    root.spawn_bg() <| () => {
      let server = accept_server(listener, require_client=false)
      defer server.close()
      let data = server.tls.read_exactly(4)
      server.tls.write(data)
      try {
        server.tls.shutdown()
      } catch {
        _ => ()
      }
    }
    let client = connect_client(
      port,
      identity=None,
      trust=testdata("server-ca.pem"),
    )
    defer client.close()
    client.tls.write(b"ping")
    inspect(bytes_text(client.tls.read_exactly(4)), content="ping")
    client.tls.shutdown()
  }
}

///|
async test "missing client identity is rejected when the server requires it" {
  let msg = try {
    @async.with_task_group() <| root => {
      let listener = listen()
      defer listener.close()
      let port = listener.addr.port()
      root.spawn_bg() <| () => {
        try {
          let server = accept_server(listener, require_client=true)
          defer server.close()
          ignore(server.tls.read_exactly(1))
        } catch {
          _ => ()
        }
      }
      @async.with_timeout(2000, () => {
        let client = connect_client(
          port,
          identity=None,
          trust=testdata("server-ca.pem"),
        )
        defer client.close()
        client.tls.write(b"x")
        ignore(client.tls.read_exactly(1))
      })
    }
    "ok"
  } catch {
    err => describe_tls_error(err)
  }
  assert_true(msg != "ok")
}

///|
async test "unrelated client certificate is rejected" {
  let msg = try {
    @async.with_task_group() <| root => {
      let listener = listen()
      defer listener.close()
      let port = listener.addr.port()
      root.spawn_bg() <| () => {
        try {
          let server = accept_server(listener, require_client=true)
          defer server.close()
          ignore(server.tls.read_exactly(1))
        } catch {
          _ => ()
        }
      }
      @async.with_timeout(2000, () => {
        let client = connect_client(
          port,
          identity=Some(
            client_id(cert="client-unrelated.pem", key="client-unrelated.key"),
          ),
          trust=testdata("server-ca.pem"),
        )
        defer client.close()
        client.tls.write(b"x")
        ignore(client.tls.read_exactly(1))
      })
    }
    "ok"
  } catch {
    err => describe_tls_error(err)
  }
  assert_true(msg != "ok")
}

///|
async test "wrong server ca is rejected even with a valid client identity" {
  let msg = try {
    @async.with_task_group() <| root => {
      let listener = listen()
      defer listener.close()
      let port = listener.addr.port()
      root.spawn_bg() <| () => {
        try {
          let server = accept_server(listener, require_client=true)
          defer server.close()
        } catch {
          _ => ()
        }
      }
      @async.with_timeout(2000, () => {
        let client = connect_client(
          port,
          identity=Some(client_id(cert="client.pem", key="client.key")),
          trust=testdata("unrelated-ca.pem"),
        )
        defer client.close()
      })
    }
    "ok"
  } catch {
    err => describe_tls_error(err)
  }
  assert_true(msg != "ok")
}

///|
async test "tls13 only mtls roundtrip succeeds" {
  @async.with_task_group() <| root => {
    let listener = listen()
    defer listener.close()
    let port = listener.addr.port()
    root.spawn_bg() <| () => {
      let server = accept_server(
        listener,
        require_client=true,
        min_protocol_version=TLS1_3_VERSION,
      )
      defer server.close()
      let data = server.tls.read_exactly(5)
      server.tls.write(data)
      try {
        server.tls.shutdown()
      } catch {
        _ => ()
      }
    }
    let client = connect_client(
      port,
      identity=Some(client_id(cert="client.pem", key="client.key")),
      trust=testdata("server-ca.pem"),
    )
    defer client.close()
    client.tls.write(b"tls13")
    inspect(bytes_text(client.tls.read_exactly(5)), content="tls13")
    client.tls.shutdown()
  }
}

///|
async test "tls13 only rejects unrelated client identity on application io" {
  let outcome = try {
    @async.with_task_group() <| root => {
      let listener = listen()
      defer listener.close()
      let port = listener.addr.port()
      root.spawn_bg() <| () => {
        try {
          let server = accept_server(
            listener,
            require_client=true,
            min_protocol_version=TLS1_3_VERSION,
          )
          defer server.close()
          ignore(server.tls.read_exactly(1))
        } catch {
          _ => ()
        }
      }
      @async.with_timeout(2000, () => {
        let client = connect_client(
          port,
          identity=Some(
            client_id(cert="client-unrelated.pem", key="client-unrelated.key"),
          ),
          trust=testdata("server-ca.pem"),
        )
        defer client.close()
        client.tls.write(b"x")
        ignore(client.tls.read_exactly(1))
      })
    }
    "ok"
  } catch {
    err => describe_tls_error(err)
  }
  assert_true(outcome != "ok")
}

///|
async test "official tls client and this package can handshake in one process" {
  @async.with_task_group() <| root => {
    let listener = listen()
    defer listener.close()
    let port = listener.addr.port()
    root.spawn_bg() <| () => {
      let server = accept_server(listener, require_client=false)
      defer server.close()
      let data = server.tls.read_exactly(7)
      server.tls.write(data)
      try {
        server.tls.shutdown()
      } catch {
        _ => ()
      }
    }
    let tcp = @socket.Tcp::connect_to_host("127.0.0.1", port~)
    defer tcp.close()
    let client = @official_tls.Tls::client(
      tcp,
      host="localhost",
      trust=@official_tls.CustomPemFile(testdata("server-ca.pem")),
    )
    defer client.close()
    client.write(b"coexist")
    inspect(bytes_text(client.read_exactly(7)), content="coexist")
    client.shutdown()
  }
}
