# B2 preparation — physical acceptance pending

This tree preserves the existing ESP32-S3 command/feedback envelope, Arduino
framework, provisioning and bounded test scripts. `origin.json` binds their
original source bytes. Historical live results belong to their original run;
this candidate has not been uploaded or tested on the board.

The board previously identified as ESP32-S3-WROOM-1 / YN038-V1.1 has 16 MiB flash
and 8 MiB PSRAM. Build with the existing Arduino ESP32 **3.3.12**,
ArduinoMqttClient **0.1.8** and ArduinoJson **7.4.3**, using:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=16M,PSRAM=opi,PartitionScheme=app3M_fat9M_16MB,CDCOnBoot=default,UploadSpeed=230400 campus_device
```

Do not upload or change networking until the field setup is ready. Keep the
original flash backup and private NVS/network files outside the repository.
Opening the existing USB UART may reset the board; preparation does not open it.
No credentials are bundled. The existing `provision.py`, `host.py`, `check_live.py`
and `check_ha.py` require explicit private configuration paths and interrupt the
lab when actually invoked. Their historical assertions are retained unchanged.
They do not certify physical output or browser clicks.

## Wiring and independent measurement

`campus_device/b2_output.h` defaults both output pins to **-1**. After reviewing
the actual board schematic/pinout and wiring, supply `B2_OUTPUT_PIN`,
`B2_APPLY_PULSE_PIN`, and `B2_WIRING_VERIFIED=1` as build flags. No onboard LED
pin is assumed. A valid ESP32 GPIO alone does not prove that it is exposed or
free of flash/PSRAM/USB/boot circuitry on this board.

Use a low-voltage, current-limited test output first, a common reference ground,
and a separate logic analyzer/counter. GPIO drives only a suitable logic input;
choose the load/driver circuit from its actual electrical specification. Record
the reviewed pins, circuit and observer model in `run.json` before enabling.

The output follows absolute ON/OFF. A second pin emits one 1 ms pulse per apply,
including a repeated *target state with a new ID*. Identical command envelopes
with the same ID do not emit a new pulse. Observe the pulse and output channels
independently. The serial `applied_count` is not the independent counter.
For a relay, GPIO pulses alone do not prove contact movement; record contacts or
another suitable physical signal and state exactly what was measured.

First calibrate two distinct IDs requesting the same target: the independent
instrument must resolve **two applies**. Then replay an identical ID: no extra
pulse is expected. Preserve raw captures and their time resolution. A counter
that sees only ON/OFF transitions cannot distinguish two ON applies.

The firmware retains only 64 recent envelopes in RAM; reboot and eviction lose
that deduplication history. Do not claim durable device-side exactly-once
execution. A host outbox ACK does not prove execution. On ambiguous physical
results preserve `unknown` and use fresh query/observation rather than silently
reissuing a non-idempotent operation.

## B2-A: HA controller

Use the existing `examples/ha_relay/controller`; do not turn it into a durable
framework. Record discovery, device/controller availability, actual browser UI
ON and OFF clicks, matching command/feedback IDs, and a fresh query. Save browser
captures and HA events alongside wire and serial logs. Service API checks do
not substitute for browser clicks. On controller/HA restart prove reconciliation
without replaying old commands.

## B2-B: independent durable consumer

`durable_consumer` uses the public library API and SQLite, separately from HA.
Set `B2_HOST`, `B2_PORT`, stable `B2_CLIENT_ID`, `B2_DB`, `B2_PREFIX`, `B2_ID`, and
`B2_ENVELOPE` (the unchanged JSON `{id,target,expires_at_ms}` envelope). The ID
and absolute deadline must remain unchanged across retries/restarts. Initial
expiry is within 30 seconds, as in the existing firmware protocol.

TLS is explicit: `B2_CA`, optional `B2_USERNAME`/`B2_PASSWORD`, and paired
`B2_CERT`/`B2_KEY`. `B2_MQTT5=1` selects MQTT5 with 300-second session expiry;
otherwise MQTT3.1.1 is used. Never remove TLS options to make an old database
open. Its stored identity must match. `broker_acknowledged` is distinct from
physical execution; the consumer prints this boundary in every terminal row.
Use one outstanding business command per evidence case and archive the outbox
safely before any administrative mutation.

`tests/b2_consumer.py` proves envelope/ID/packet-ID/DUP preservation across a host
process crash and native reopening. `tests/durable_crash_boundaries.py` proves
six exact host windows with test-executable-only SQLite callbacks plus a raw
peer. These are process-crash evidence; they do not emulate power loss, device
flash persistence, or physical output. Keep the original real-Mosquitto durable
crash tests as a separate oracle.

## Run binding and evidence

```sh
python examples/esp32/b2_evidence.py prepare --candidate CANDIDATE.zip \
  --firmware campus_device.ino.bin --consumer durable_consumer.exe --output NEW_PRIVATE_RUN
python examples/esp32/b2_evidence.py classify NEW_PRIVATE_RUN
```

Complete `run.json` with non-secret environment, board/wiring and calibration.
For each command, append `{id,case}` to `commands.jsonl`. Independent observations
in `physical.jsonl` use `id`, `apply_count`, `independent_observer`,
`coverage_complete`, and `raw_capture: {file,sha256}`. Calibration uses the same
capture identity plus `two_distinct_applies_resolved`. Missing calibration,
capture or interval coverage stays `evidence_insufficient`; reported counts
alone are not proof. Classification never automatically declares full B2 pass.
Do not upload private broker credentials, device settings or recovery archives.

Required cases: baseline; identical ID; conflicting ID; expired envelope;
**actual retained replay on resubscription**; device network outage; broker
normal stop and kill; controller and HA restart; session expiry; withholding the
**complete** PUBACK; six durable host crash windows. For retained replay, retain
while the device is offline, reconnect/resubscribe, verify retain flag/rejection,
and remove the test retained value afterward. Partial ACK bytes are a different
fault. Label maintenance reasons and H01 as raw-peer cases; broker kill does
not establish a specific MQTT5 DISCONNECT reason. Record zero/one/multiple/
insufficient evidence separately for every command and each B2 path.
