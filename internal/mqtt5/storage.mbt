///|
/// Canonical, bounded PUBLISH property section for the concrete outbox. The
/// bytes include Property Length and are validated for client-to-server use.
/// This is a wire-value serializer, not a storage backend or schema migration.
pub fn encode_publish_properties(
  properties : Array[Property],
  limit : Int,
) -> Bytes raise CodecError {
  if limit < 1 { raise Malformed("property bound must be positive") }
  let bytes = property_bytes(properties, 3, limit)
  if bytes.length() > limit { raise Malformed("properties exceed storage bound") }
  bytes
}

///|
/// Decode only one complete client PUBLISH property section. Recovered bytes
/// never bypass packet-specific validation or accept trailing hidden metadata.
pub fn decode_publish_properties(
  bytes : Bytes,
  limit : Int,
) -> Array[Property] raise CodecError {
  if limit < 1 || bytes.length() > limit {
    raise Malformed("properties exceed storage bound")
  }
  let cursor : Cursor = { bytes, position: 0, end: bytes.length() }
  let properties = cursor.properties(3, false)
  if cursor.position != cursor.end { raise Malformed("trailing property bytes") }
  properties
}
