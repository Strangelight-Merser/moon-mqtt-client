# 0.8.1 software release acceptance

Status: **published and verified on 2026-10-31**. The nine supplied software
acceptance requirements are satisfied by this project's evidence. Organizer
acceptance remains the organizer's decision; physical B2 is still unaccepted and
outside this software delivery. No additional form or organizer message was sent.

## Final release identity

- Tested source: `af6c9f6da723e20bedb06c016246cc287817a2f8`.
- [PR #13](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/13) merged
  preserving development history; tag `v0.8.1` is
  `bcaecd6f2a2e66150344739c9eba8c2a094cfc7e`. The merge tree exactly matches the
  tested tree (`3b46c5b8045ad0f5c387c1e1f281431bf67a5755`).
- [Mooncakes 0.8.1](https://mooncakes.io/docs/Strangelight-Merser/moon-mqtt-client)
  was actually uploaded with HTTP 200 at `2026-10-31T20:13:11Z`; registry build
  status is success and latest version is 0.8.1.
- Published library ZIP SHA256:
  `74499dffa07c5b5e8ab6d38d6c3a78e3781983d9a94efefc8e39461966476d78`.
  It exactly equals the hosted macOS candidate ZIP. Linux's archive hash differs
  (`bdcd55890f0678ca38810f373b1df8b834e6e597912652cc323f342e4540eee2`), but all
  **324 filenames and file payloads** match the published package.
- [GitHub v0.8.1](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.8.1)
  contains nine attachments: library, TLS dependency, source, Linux/macOS full
  acceptance evidence, diagnostic evidence, publication verification, summary and
  SHA256SUMS. Every attachment was downloaded anonymously and matched the local
  bytes and GitHub SHA256 digest.
- This completion record is a documentation-only follow-up. The release tag,
  published ZIPs and original CI evidence are not rewritten.

## Final verification

| Check | Result |
|---|---|
| Toolchain | Pinned `moonc 0.10.17+d971670cd`, `moon 0.1.20261023`; official archive hashes verified |
| Core | [41230181593](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41230181593), both platforms passed; 165/165 native tests |
| Integration | [41230143590](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41230143590), Linux 37 passed; macOS 26 passed, 11 explicitly not applicable |
| Full Release | [41230182001](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41230182001), first final-source attempt: Linux 46 passed; macOS 35 passed, 11 explicitly not applicable |
| Benchmarks/E06 | Each platform: 39 trials, three trace A/B pairs, `within_trigger`; original budgets and thresholds retained |
| Long recovery | Each platform: TCP and mTLS each 1800 seconds and 100 recoveries; recovery/resource gates passed |
| Frozen source and evidence | Source unchanged, complete evidence inventory passed, downloaded hashes rechecked |
| Latest compiler | Rolling-stable check/build/test passed in the full Release workflow |
| Extracted consumers | Local library 34/34 and operator 9/9 passed; each hosted Release validates its own candidate |
| Published registry consumers | Fresh temporary modules, `MOON_WORK` removed, no local override: TCP/mTLS QoS1 exchanges and 34/34 behavior tests; zero errors/failures/skips |
| TLS dependency | 0.1.1 published with HTTP 200; standalone 14/14 tests and unchanged handshake example from a fresh registry installation passed |
| Runnable example | All four `examples/mqtt_demo/demo.py` scenarios passed with a real broker and independent simulated device |
| Local handoff | Original project is on main with MoonBit 0.10.17; its actual `./scripts/check.sh core` passed all 17 stages with unchanged source identity |

macOS's 11 exclusions are Linux-only integration scenarios, executed on Linux.
The macOS registry suite excludes EMQX Docker interoperation but still runs raw WS
framing. Compilation retains dependency/API deprecation and unused-code warnings;
this is not a warning-free-build or full-protocol-certification claim.

## Nine supplied requirements

1. MoonBit implements the runtime and native examples; compiler 0.10.17 exceeds 0.10.16.
2. GitHub is public and development commits are preserved by merge commits.
3. Source modules and declared core behavior are exercised by both-platform acceptance.
4. README describes the goal, fixed installation, OpenSSL requirement, usage and reproducible examples.
5. CI covers check, build and tests, with separate integration and full Release levels.
6. The four-scenario runnable example passed.
7. Core, protocol faults, certificate rejection, recovery, persistence and independent consumers passed.
8. Mooncakes 0.8.1 is published and was freshly installed and tested.
9. Apache-2.0 and applicable upstream NOTICE/LICENSE attribution are included.

Current checkout: `/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client`. The previous
compiler remains at `.tools/moon-0.10.12-before-v0.8.0`. Unrelated worktrees and
physical hardware were not changed. Local evidence is under
`_build/release-v0.8.1/` in the release-record worktree; durable copies are attached
to GitHub v0.8.1. The earlier 0.8.0 publication and failures remain available.

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
TLS-1.3-only QoS1 and negative certificate matrix all passed. [Hosted source confirmation](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41229340498)
passed all original selected TLS cases and 165 native tests on all three runners,
including image `20261026.0455.2`. On that same image the isolated registry 0.8.0
control still reported the original decode error; the patched source passed.
[Core](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41229340521)
and [Integration](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41229340501)
also passed on Linux/macOS. These establish the TLS fix, not a final 0.8.1 release pass.

The standalone TLS module passed 14/14 original tests and was published as 0.1.1
with HTTP 200. Registry build status is success; its ZIP SHA256 is
`1bdaf7c312a3f3f49cd5de5907dc5718a03f71202b03ab8081bb57d30987d78a`.
Packaging it inside the parent workspace first produced an empty ZIP because the
parent `.moonignore` excludes `third_party/`; publication refused before upload.
The successful package was built from an identical standalone source copy outside
that parent ignore scope. This packaging failure and logs are retained.

A delayed GitHub status snapshot briefly looked like a stalled native test;
completed logs show the original run passed 165/165. Extra per-case diagnostics
were canceled as unnecessary, not used to replace an acceptance failure.

The fresh final-source Release and publication results are recorded above.
