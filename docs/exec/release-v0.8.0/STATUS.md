# 0.8.0 software release acceptance

User authorization on 2026-10-31 covers the previously proposed toolchain upgrade,
final software integration, documentation, GitHub/Mooncakes publication and clean
registry consumer verification. The nine supplied competition requirements govern
this delivery. Physical B2 remains a separate, unaccepted scope; prior hardware
HOLD records are retained as historical evidence.

Working branch: `codex/competition-release-v0.8.0` in the existing
`moon-mqtt-release-record` worktree. Baseline: `8933416`; no hardware-operation
commits are included. Fixed compiler: `0.10.17+d971670cd`.

Current status: **superseded by the verified 0.8.1 release**. The original
0.8.0 publication and tests remain recorded below. The post-merge macOS system
LibreSSL issue was reproduced independently and fixed by using Homebrew OpenSSL 3.
See [0.8.1 acceptance](../release-v0.8.1/STATUS.md) for the new full Release run,
actual registry publication and clean installation results. Physical B2 remains
outside this software delivery.

## Post-merge finding

[Integration 41227465200](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41227465200)
passed Linux but failed nine positive macOS mTLS cases and its source consumer.
The source tree is identical to the accepted candidate. The failed runner image
is `macos-26-arm64 20261026.0455.2`; the passed full Release used
`20261013.0424.1`. These facts do not yet identify the cause. No retry has replaced
the failure. The original broker logs and failed artifact are preserved. The
previous full Release and post-publication registry passes remain true, but do
not establish compatibility with this newly observed environment.

## Published identity

- Tested source: `af0fb80180d0a9c8ef828479053191909c622a11`.
- PR [#12](https://github.com/Strangelight-Merser/moon-mqtt-client/pull/12) merged
  with development history preserved. Tag `v0.8.0` points to
  `e30f2baa3029ef9a4a5b5653c57fafedc25980b4`; its Git tree exactly equals the tested tree.
- [GitHub v0.8.0](https://github.com/Strangelight-Merser/moon-mqtt-client/releases/tag/v0.8.0)
  contains the library ZIP, tagged source, both full CI evidence archives,
  publication verification, acceptance summary and SHA256SUMS.
- [Mooncakes 0.8.0](https://mooncakes.io/docs/Strangelight-Merser/moon-mqtt-client):
  actual upload returned **HTTP 200**, registered at `2026-10-31T18:03:24Z`;
  the registry reports `build_status=success`, version/latest version `0.8.0`.
- Published library SHA256:
  `42d84459f9d491a08204d4d1b16047b0c1c957a7f6634837cd74fa8c0b009de8`.
  This equals the local and hosted macOS accepted ZIP. The Linux ZIP has different
  archive bytes (`2500a22425af4daada53674a418d04f7f0f5a03bed7422e5d075ad3d7b439226`);
  all **322 filenames and file payloads** are identical across the three packages.
- These completion notes follow the release as a documentation-only update;
  the released source/package and original CI evidence remain immutable.

## Verification

| Check | Result |
|---|---|
| Pinned compiler | `moonc 0.10.17+d971670cd`, `moon 0.1.20261023`; official archive hashes checked |
| Core CI | [41207184937](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41207184937), Linux/macOS passed; native tests 165/165 |
| Integration CI | [41207181315](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41207181315), Linux 37 passed; macOS 26 passed and 11 explicitly not applicable |
| Full Release CI | [41207207014](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41207207014), first final-source attempt: Linux 46 passed; macOS 35 passed and 11 explicitly not applicable |
| Benchmarks and E06 | 39 trials per platform; three trace A/B pairs; `within_trigger` on both platforms; original thresholds retained |
| Long recovery | Each platform: TCP and mTLS each 1800 seconds and 100 recoveries; resource and recovery gates passed |
| Source/evidence integrity | Frozen source unchanged; candidate consumers and complete evidence inventory passed; downloaded inventory hashes rechecked |
| Latest compiler | Rolling-stable compatibility check/build/test passed in the full Release workflow |
| Candidate installation | Local extracted library 34/34 and operator 9/9 passed; hosted Release also validates its own extracted candidates |
| Published registry installation | Fresh temporary consumers, `MOON_WORK` removed, no local workspace override: TCP/mTLS QoS1 round trips plus 34/34 behavior cases, zero skips/errors/failures |
| Runnable example | All four `examples/mqtt_demo/demo.py` scenarios passed against the real broker and independent simulated device |
| License/public access | Public GitHub repository; Apache-2.0, NOTICE and upstream WebSocket attribution included |

macOS excludes the 11 Linux-only integration stages explicitly; Linux runs those
stages. Its registry consumer suite excludes EMQX Docker interoperation; raw WS
framing still runs. Software simulation is not physical B2 acceptance. Compilation
passes with existing dependency/API deprecation and unused-code warnings; this
release does not claim a warning-free build or full MQTT protocol certification.

Evidence is retained under `_build/release-v0.8.0/` and timestamped
`_build/acceptance/` directories, with durable public copies attached to the
GitHub release. Original tests, scenario list, budgets and historical failures
remain unchanged. No previous release's pass is assigned to this build.

## Local handoff and public downloads

All seven GitHub release attachments were downloaded anonymously. Their bytes
match the local assets and GitHub's SHA256 digests. The original working directory
`/Users/huaiyi/Documents/ChatGPT/moon-mqtt-client` is on `main` with the fixed
0.10.17 toolchain. Its actual `./scripts/check.sh core` entry passed after the
switch, with unchanged source identity. The previous compiler is preserved at
`.tools/moon-0.10.12-before-v0.8.0`; unrelated worktrees and hardware files were
not changed. Included draft PRs #8–#11 were closed after their heads were verified
as ancestors of merged main.

## Requirement mapping

1. MoonBit implements the runtime and examples; the pinned compiler exceeds 0.10.16.
2. The GitHub repository is public and the merge preserves development history.
3. Core behavior is covered by the Linux/macOS acceptance suites and support matrix.
4. README documents the goal, pinned installation, usage and reproducible examples.
5. CI executes check, native tests and build, plus integration and Release suites.
6. The four-scenario runnable demonstration passed.
7. Core, negative protocol, certificate, recovery, persistence and consumer paths passed.
8. Mooncakes 0.8.0 is actually published and independently installed/tested.
9. Apache-2.0 and applicable upstream attribution are included.

The supplied organizer notice says approved contestants need no further form.
No additional form, signature or organizer message was sent. Final organizer
acceptance and physical B2 remain separate from this software completion.

## First-attempt finding

`f5ff954` passed every Core scenario on Linux and macOS, but correctly failed the
source-identity gate. MoonBit 0.10.17 writes generated API descriptors during the
build: seven tracked descriptors use the explicit `Int32` spelling instead of
`Int`, and four executable packages lacked descriptors. Local reproduction and
the downloaded Linux source/source-end manifests identify exactly the same
files. No test assertions or implementation changed. The generated descriptors
are committed before restarting acceptance; the source-freeze gate is unchanged.

First push run: [41205820711](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41205820711).
Its failed status and original artifacts are retained. The first full Release
run was canceled after this invalid source snapshot was identified, before
spending the long-soak budget. Local initial index download interruption is
recorded separately; it did not execute code tests.

## Cold compiler startup diagnosis

The original macOS Integration and push Core at `aa38a7f` timed out at the first
raw-protocol `moon run` invocation (8 seconds); the same-source PR Core passed.
The new compiler's `moon build` uses `-g -O0`, while `moon run` uses `-O0
-stacktrace`, invalidating the shared dependency build cache. An isolated hosted
[diagnostic](https://github.com/Strangelight-Merser/moon-mqtt-client/actions/runs/41206987665)
reproduced a cold 8-second timeout, retained its captured stderr, then passed all
10 original cases in 3.03 seconds after an explicit `moon run --build-only`.
The diagnostic is not a replacement acceptance run. Original failed runs remain.

The acceptance runner now records a required build-only preparation before the
raw protocol suite. Original test files, 5-second peer accept deadline, 8-second
process deadline, and assertions remain unchanged. This separates compilation
from protocol timing rather than increasing the budget. The temporary diagnostic
workflow is removed after retaining its run and result.
