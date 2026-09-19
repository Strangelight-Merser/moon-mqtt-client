# Upstream provenance

This internal native WebSocket client is derived from the `websocket` package
in `moonbitlang/async` 0.21.3 (Apache-2.0). The source snapshot came from the
pinned local Mooncakes dependency, not a later upstream revision.

Only the client frame engine, common types, and utilities were copied. The
fork replaces the HTTP-client-only handshake with a bounded handshake over a
generic closable reader/writer, requires the `mqtt` subprotocol, rejects masked
server frames, and omits the active WebSocket ping waiter that depends on an
upstream internal coroutine package. Automatic replies to peer PING frames are
retained. Cryptographic handshake and masking entropy comes from
`Strangelight-Merser/async-tls`; entropy failure is propagated.

Normal peer Close frames receive a Close response, and a successful MQTT
DISCONNECT waits for the broker to close the network, bounded by the configured
write timeout; it does not race queued MQTT frames with an immediate Close. Once malformed framing is
detected, this MQTT-only fork closes the underlying transport synchronously and
raises the recorded protocol error. It does not risk an unbounded response write
to a malformed peer.
