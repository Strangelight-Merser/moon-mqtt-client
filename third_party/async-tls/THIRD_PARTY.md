# Upstream provenance

Module `Strangelight-Merser/async-tls` 0.1.0 is derived from the TLS package
in `moonbitlang/async` **0.21.3** (Apache-2.0).

- Upstream repository: https://github.com/moonbitlang/async
- Upstream module version used as the source: `0.21.3`
- Upstream license: Apache-2.0 (copied as `LICENSE`)
- Files were taken from the local Mooncakes copy used by moon-mqtt-client,
  not from a later `main` commit.

## Copied files and SHA-256 of the unmodified upstream bytes

| File | SHA-256 |
|---|---|
| tls.mbt | `82a17d0ce86b487e89b21560db2bd0ffe0785115755aa3a34b01f9d0ba40d32a` |
| openssl.mbt | `9d15e9813bc2aa5a573f9cc6d460eab24e2041d7dbcd3af96e38c563500d4e30` |
| openssl.c | `d20f09c5dd84e7a132d7c66cafb881099d3a6007ea45ecb9c05c0018152ae925` |
| openssl_loader.mbt | `5a823cf171f2cf6c9ec3cf6be938db959c450c7cf102196fbfb7695bb96e3002` |
| transport.mbt | `ec31971e4e228b9ead00fcda32b4b1a5858970ab8337b0636fe3249d6db3ca14` |
| crypto_util.mbt | `802c49402819a5bfab15c70703312634440768da8656f968a422536132f03bbd` |

Windows Schannel sources, Wasm backends, and upstream TLS tests were not
copied. This first version supports macOS and Linux native clients only.

## Patches applied on top of the copied files

1. Rename every C export from `moonbitlang_async_*` / `moonbitlang_async_tls_*`
   to `strangelight_async_tls_*`, and rename the custom BIO method to
   `strangelight/async-tls`, so this library can link in the same process as
   official `moonbitlang/async/tls`.
2. Add `ClientIdentity` and load a PEM certificate chain plus unencrypted
   private key onto a dedicated `SSL_CTX` for that connection.
3. Reject encrypted private keys by inspecting PEM headers and by installing a
   password callback that never prompts.
4. Check that the certificate and private key match before the handshake.
5. Allow a test/server helper to request and verify client certificates.
6. Official `moonbitlang/async/internal/*` packages cannot be imported from
   another module. The TLS BIO path therefore vendors a local `CBuffer` and
   `IoBuffer` with `strangelight_async_tls_` C symbols, and still uses the
   official public `io` / `fs` / `socket` / task APIs.

Identity loading uses OpenSSL `SSL_CTX_use_certificate_chain_file`,
`SSL_CTX_use_PrivateKey_file`, `SSL_CTX_check_private_key`, and
`SSL_CTX_set_default_passwd_cb`. Client identity does not cap the TLS
protocol version. Test-only minimum-version limits use `SSL_CTX_ctrl`
(`SSL_CTRL_SET_MIN_PROTO_VERSION`); `SSL_CTX_set_max_proto_version` is a
macro on OpenSSL 3 and is not imported through `dlsym`.

## Release validation fixes (2026-09-16)

- Read cancellation no longer marks the transport permanently failed.
- Closed TLS handles reject reads/writes before calling OpenSSL.
- Independent regression cases cover idle-read cancellation and closed-handle access.
