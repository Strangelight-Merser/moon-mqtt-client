# 0.8.1 software release acceptance

Status: preparing and validating; not published. Fresh evidence will bind this
version; the 0.8.0 pass is not transferred to it. The user's authorization covers
completing the software acceptance and publication, including this discovered fix.
Physical B2 remains unaccepted and outside this delivery.

## Reason for the patch

After 0.8.0 passed full Linux/macOS acceptance and was published, newer macOS
runner image `20261026.0455.2` failed nine positive mTLS cases and the independent
source consumer. The same source had passed on image `20261013.0424.1`.

Bounded image comparisons reproduced the failure in both the original unchanged
MQTT test and a fresh registry 0.8.0 consumer. Native error:
`error:1404B41A:SSL routines:ST_CONNECT:tlsv1 alert decode error`.
Certificate and private-key loading succeeded. An independent `/usr/bin/openssl`
client also failed TLS 1.3 mTLS on affected images, while Homebrew OpenSSL and
TLS 1.2 diagnostic controls succeeded. The broker linked Homebrew OpenSSL 4.
This isolates a system LibreSSL interoperability failure; it is not repaired by
loosening tests or downgrading TLS.

Evidence: [original post-merge failure](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41227465200),
[bounded original/registry comparison](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41228672249),
[independent TLS controls](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41228967847).
Failed runs remain intact. Diagnostic-only pushes' redundant normal CI runs were
canceled; diagnostic results are not counted as release acceptance.

## Change

`Strangelight-Merser/async-tls@0.1.1` loads the explicitly documented Homebrew
OpenSSL 3 installation on macOS. The system LibreSSL paths are removed. Linux's
existing loader, protocol settings, certificate verification and original tests
are unchanged. The MQTT module pins this dependency as version 0.8.1.

Local preliminary checks: 165/165 native tests; original positive mTLS,
TLS-1.3-only QoS1 and negative certificate matrix all passed. Hosted source
confirmation and full fresh release acceptance are still required.
