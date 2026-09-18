///|
/// Independent consumer of `Strangelight-Merser/async-tls`.
/// Completes an mTLS handshake over TCP and echoes four bytes.
async fn main {
  @async.with_task_group() <| root => {
      let listener = @socket.TcpServer::TcpServer(
        @socket.Addr::parse("127.0.0.1:0"),
      )
      defer listener.close()
      let port = listener.addr.port()
      root.spawn_bg() <| () => {
        let (tcp, _) = listener.accept()
        defer tcp.close()
        let server = @tls.Tls::server(
          tcp,
          private_key_file="testdata/server.key",
          private_key_type=@tls.PEM,
          certificate_file="testdata/server.pem",
          certificate_type=@tls.PEM,
          client_ca_file="testdata/client-ca.pem",
          require_client_certificate=true,
        )
        defer server.close()
        let data = server.read_exactly(4)
        server.write(data)
        try {
          server.shutdown()
        } catch {
          _ => ()
        }
      }
      let tcp = @socket.Tcp::connect_to_host("127.0.0.1", port~)
      defer tcp.close()
      let client = @tls.Tls::client(
        tcp,
        host="localhost",
        trust=@tls.CustomPemFile("testdata/server-ca.pem"),
        identity={
          certificate_chain_file: "testdata/client.pem",
          private_key_file: "testdata/client.key",
        },
      )
      defer client.close()
      client.write(b"ping")
      let echoed = client.read_exactly(4)
      guard echoed == b"ping" else { abort("unexpected echo") }
      client.shutdown()
      println("mtls handshake ok")
    }
}
