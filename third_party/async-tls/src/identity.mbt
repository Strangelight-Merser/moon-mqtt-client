// Copyright 2025 International Digital Economy Academy
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
/// Extract a `TlsError` message for tests and callers that caught `Error`.
pub fn describe_tls_error(err : Error) -> String {
  try {
    raise err
  } catch {
    TlsError(msg) => msg
    _ => "\{err}"
  }
}

///|
/// PEM files presented by a TLS client during handshake.
///
/// `certificate_chain_file` is the leaf certificate followed by any
/// intermediate certificates required to reach a CA the peer trusts.
/// `private_key_file` must be an unencrypted PEM private key. Encrypted keys
/// are rejected without prompting for a passphrase.
pub(all) struct ClientIdentity {
  certificate_chain_file : String
  private_key_file : String
}

///|
#cfg(not(platform="windows"))
fn private_key_is_encrypted(text : String) -> Bool {
  text.contains("BEGIN ENCRYPTED PRIVATE KEY") ||
  text.contains("Proc-Type: 4,ENCRYPTED")
}

///|
#cfg(not(platform="windows"))
extern "C" fn SSL_CTX::reject_encrypted_keys(self : SSL_CTX) = "strangelight_async_tls_ssl_ctx_reject_encrypted_keys"

///|
#cfg(not(platform="windows"))
#borrow(file)
extern "C" fn SSL_CTX::use_certificate_chain_file(
  self : SSL_CTX,
  file : Bytes,
) -> Int = "strangelight_async_tls_ssl_ctx_use_certificate_chain_file"

///|
#cfg(not(platform="windows"))
#borrow(file)
extern "C" fn SSL_CTX::use_private_key_file(
  self : SSL_CTX,
  file : Bytes,
  file_type : X509FileType,
) -> Int = "strangelight_async_tls_ssl_ctx_use_private_key_file"

///|
#cfg(not(platform="windows"))
extern "C" fn SSL_CTX::check_private_key(self : SSL_CTX) -> Int = "strangelight_async_tls_ssl_ctx_check_private_key"

///|
#cfg(not(platform="windows"))
extern "C" fn SSL_CTX::require_client_certificate(self : SSL_CTX) = "strangelight_async_tls_ssl_ctx_require_client_certificate"

///|
/// Set the minimum protocol version through `SSL_CTX_ctrl`. OpenSSL 3 exposes
/// `SSL_CTX_set_min_proto_version` only as a macro, so it cannot be `dlsym`'d.
#cfg(not(platform="windows"))
extern "C" fn SSL_CTX::set_min_proto_version(
  self : SSL_CTX,
  version : Int,
) -> Int = "strangelight_async_tls_ssl_ctx_set_min_proto_version"

///|
#cfg(not(platform="windows"))
extern "C" fn SSL::require_client_certificate(self : SSL) = "strangelight_async_tls_ssl_require_client_certificate"

///|
#cfg(not(platform="windows"))
async fn read_text_file(path : String, what~ : String) -> String raise {
  try {
    @fs.read_file(path).text()
  } catch {
    err =>
      raise TlsError("client identity: failed to read \{what} \{path}: \{err}")
  }
}

///|
#cfg(not(platform="windows"))
async fn SSL_CTX::load_pem_trust(self : SSL_CTX, path : String) -> Unit raise {
  let pem = try {
    @fs.read_file(path).text()
  } catch {
    err => raise TlsError("failed to read trust file \{path}: \{err}")
  }
  for cert in decode_pem_certificates(pem) {
    if self.add_root_certificate(cert) != 1 {
      raise TlsError(err_get_error())
    }
  }
}

///|
#cfg(not(platform="windows"))
async fn SSL_CTX::apply_client_identity(self : SSL_CTX, identity : ClientIdentity) -> Unit raise {
  self.reject_encrypted_keys()
  let key_text = read_text_file(
    identity.private_key_file,
    what="private key file",
  )
  guard !private_key_is_encrypted(key_text) else {
    raise TlsError("client identity: encrypted private keys are not supported")
  }
  let certificate_chain_file = @utf8.encode(identity.certificate_chain_file)
  if self.use_certificate_chain_file(certificate_chain_file) != 1 {
    raise TlsError(
      "client identity: failed to load certificate chain: " + err_get_error(),
    )
  }
  let private_key_file = @utf8.encode(identity.private_key_file)
  if self.use_private_key_file(private_key_file, PEM) != 1 {
    raise TlsError(
      "client identity: failed to load private key: " + err_get_error(),
    )
  }
  if self.check_private_key() != 1 {
    raise TlsError("client identity: certificate and private key do not match")
  }
}
