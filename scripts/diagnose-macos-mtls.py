#!/usr/bin/env python3
"""One-shot post-release diagnosis; original acceptance tests are unchanged."""
import ctypes
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("integration", ROOT / "tests/integration/run.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
broker = h.Broker(mtls=True)
broker.start()
try:
    for command in (["sw_vers"], ["which", "openssl"], ["openssl", "version", "-a"],
                    ["otool", "-L", shutil.which("mosquitto")],
                    ["openssl", "x509", "-in", str(broker.temp / "client.pem"), "-noout", "-text"]):
        subprocess.run(command, check=True)
    for executable in ("/usr/bin/openssl", shutil.which("openssl")):
        for protocol in ([], ["-tls1_2"]):
            command = [executable, "s_client", "-connect", f"127.0.0.1:{broker.port}",
                       "-servername", "localhost", "-CAfile", str(broker.temp / "ca.pem"),
                       "-cert", str(broker.temp / "client.pem"), "-key", str(broker.temp / "client.key"),
                       *protocol]
            print("RAW_TLS_CONTROL", command, flush=True)
            try:
                result = subprocess.run(command, input="", text=True, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, timeout=10)
                print(result.stdout, "RAW_TLS_EXIT", result.returncode, flush=True)
            except subprocess.TimeoutExpired as error:
                print("RAW_TLS_TIMEOUT", error.stdout, flush=True)
    for name in ("/usr/lib/libssl.48.dylib", "/usr/lib/libssl.46.dylib"):
        try:
            lib = ctypes.CDLL(name)
        except OSError as error:
            print(name, error, flush=True)
            continue
        lib.OpenSSL_version.argtypes = [ctypes.c_int]
        lib.OpenSSL_version.restype = ctypes.c_char_p
        print("loaded", name, lib.OpenSSL_version(0), flush=True)
        lib.TLS_client_method.restype = ctypes.c_void_p
        lib.SSL_CTX_new.argtypes = [ctypes.c_void_p]
        lib.SSL_CTX_new.restype = ctypes.c_void_p
        ctx = lib.SSL_CTX_new(lib.TLS_client_method())
        lib.SSL_CTX_use_certificate_chain_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.SSL_CTX_use_PrivateKey_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.ERR_get_error.restype = ctypes.c_ulong
        lib.ERR_error_string.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
        lib.ERR_error_string.restype = ctypes.c_char_p
        for label, result in (
            ("certificate", lib.SSL_CTX_use_certificate_chain_file(ctx, os.fsencode(broker.temp / "client.pem"))),
            ("private key", lib.SSL_CTX_use_PrivateKey_file(ctx, os.fsencode(broker.temp / "client.key"), 1)),
        ):
            print("ctypes", name, label, result, lib.ERR_error_string(lib.ERR_get_error(), None), flush=True)
        lib.SSL_CTX_free.argtypes = [ctypes.c_void_p]
        lib.SSL_CTX_free(ctx)
    with tempfile.TemporaryDirectory(prefix="mqtt-mtls-diagnosis-") as temporary:
        work = Path(temporary)
        (work / "moon.mod").write_text('name = "diagnostic/mtls"\npreferred_target = "native"\nimport { "Strangelight-Merser/moon-mqtt-client@0.8.0", "moonbitlang/async@0.21.3", }\n')
        (work / "moon.pkg").write_text('import { "Strangelight-Merser/moon-mqtt-client" @mqtt, "moonbitlang/async", }\nsupported_targets = "+native"\npkgtype(kind: "executable")\n')
        source = '''async fn main {
  try {
    @mqtt.with_client(@mqtt.Config::new("localhost", "mtls-diagnosis", port=PORT,
      tls=@mqtt.CustomCA("CA"), client_identity=Some({
        certificate_chain_file: "CERT", private_key_file: "KEY",
      })), async fn(client) {
        let granted = client.subscribe([{ topic: "diagnostic/mtls", qos: @mqtt.AtLeastOnce }])
        assert_true(granted == [@mqtt.Granted(@mqtt.AtLeastOnce)])
        println("mTLS connected and subscribed")
      })
  } catch {
    @mqtt.TlsFailure(message) => { println("TLS_DETAIL=" + message); abort(message) }
    error => { println("OTHER=" + error.to_string()); abort("unexpected error") }
  }
}
'''.replace("PORT", str(broker.port)).replace('"CA"', '"' + str(broker.temp / "ca.pem") + '"').replace('"CERT"', '"' + str(broker.temp / "client.pem") + '"').replace('"KEY"', '"' + str(broker.temp / "client.key") + '"')
        (work / "main.mbt").write_text(source)
        subprocess.run([os.environ["MOON"], "update"], cwd=work, check=True)
        result = subprocess.run([os.environ["MOON"], "run", ".", "--target", "native"], cwd=work)
        print("native diagnostic exit", result.returncode, flush=True)
        raise SystemExit(result.returncode)
finally:
    broker.stop()
    print((broker.temp / "broker.log").read_text(), flush=True)
    broker.close()
