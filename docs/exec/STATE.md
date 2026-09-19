# Execution state

Updated2026-09-18 local / 2026-09-19T02:19:45Z UTC. v0.3 release: **done**.

The user explicitly authorized the prepared publication sequence. PR#2 had already been merged by the repository owner; no repeat merge was needed. Accepted source eeb93be40ba19020df1a9effcdd8771c5f132104 and merged/tagged a7fe72350831f3af409f36b3c78499cb141bf111 have identical Git tree1e54ac08f5e07323a609f3cc3dc89ba6d526baf5. [Exact-source CI](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/35356886328) passed on macOS/Ubuntu, including Linux EMQX.

v0.3.0 published at 2026-09-19T02:19:45Z: [https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.3.0](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.3.0). `Strangelight-Merser/async-tls@0.1.0` was published first, followed by `Strangelight-Merser/moon-mqtt-client@0.3.0`; both publish commands returned200 OK. Unmodified `tests/consumer_smoke.py --registry` then passed TCP and mTLS QoS1 round trips in fresh temporary modules without local workspace overrides. Four public release assets were downloaded without authentication and matched both local bytes and GitHub SHA256 digests.

## Evidence and packaging

Root verified the two ZIPs against every source file (81 MQTT,18 TLS), and publish itself checked extracted packages. TLS must be packaged/published from the standalone module: a direct nested-workspace package produced an empty ZIP, which was rejected and retained separately. Published hashes:

- async-tls0.1.0: e27c09cc7530b906da4978deaf6a2ba41c1aa4b87d7ab791348e978099d8d9c1
- moon-mqtt-client0.3.0: 2d18a13e499ff4ee7d9794021855e5f387ddf572b0a3e0902a7ee3f1e4287dc1
- Source archive: 2ea0755ccd43f0ae41a1da6bfe001b62d7e51d21896a1e37edbfc25665fde974

Logs/artifacts reside in `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-roadmap/_build/exec-mqtt5/`: `publish-tls01-result.json`, `publish-mqtt03.log`, `registry-v03-published-acceptance.log`, `github-release-v03.json`, `public-download-verification.json`, `release-assets/`. First anonymous urllib retrieval timed out during TLS handshake after retrieving the source archive; bounded curl IPv4 retrieval completed the remaining assets with normal certificate verification. No failed download was called successful.

## Scope and handoff

This documentation-only release record changes no accepted implementation or tag. Earlier implementation/review history is retained in Git and TASKS. Publication was performed by root; no new subagent run or confirmed actual-model metadata is claimed. Prior requested Sol/Luna roles remain in TASKS.

The authorized host roadmap (WS/WSS, recoverable QoS1, SQLite outbox, HA host consumer, MQTT5 subset) is completed separately on [codex/roadmap-native](https://github.com/Strangelight-Merser/moon-mqtt-client/tree/codex/roadmap-native), accepted d5e5fb0/CI35414746155. Those features were not silently published as0.3.0. Their STATE/TASKS own next version/integration decisions. Actual ESP32/HA-instance validation still requires board/pins/access; no real hardware claim. Do not repeat completed release actions.
