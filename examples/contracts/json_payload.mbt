///|
/// The canonical payload used by every example contract for `online`/`offline`
/// availability. Kept here so availability has one wire representation.
pub fn availability_json(online : Bool) -> String {
  json_encode_string(if online { "online" } else { "offline" })
}

///|
/// Escape a JSON string literal, including the quote, backslash and every
/// C0 control character. Output is always a quoted JSON string.
pub fn json_encode_string(value : String) -> String {
  let out = StringBuilder()
  json_append_string(out, value)
  out.to_string()
}

///|
/// Append one quoted JSON string literal to `out`.
pub fn json_append_string(out : StringBuilder, value : String) -> Unit {
  out.write_char('"')
  for ch in value {
    match ch {
      '"' => out.write_string("\\\"")
      '\\' => out.write_string("\\\\")
      '\u{08}' => out.write_string("\\b")
      '\u{0C}' => out.write_string("\\f")
      '\n' => out.write_string("\\n")
      '\r' => out.write_string("\\r")
      '\t' => out.write_string("\\t")
      _ =>
        if ch.to_int() < 0x20 {
          out.write_string("\\u")
          let code = ch.to_int()
          for shift in [12, 8, 4, 0] {
            let nibble = (code >> shift) & 0xF
            if nibble < 10 {
              out.write_char((nibble + 48).unsafe_to_char())
            } else {
              out.write_char((nibble + 87).unsafe_to_char())
            }
          }
        } else {
          out.write_char(ch)
        }
    }
  }
  out.write_char('"')
}

///|
/// Append a JSON object. Fields are written in the order given, so the wire
/// payload is deterministic and reviewable instead of depending on map order.
pub fn json_append_object(
  out : StringBuilder,
  fields : Array[(String, String)],
) -> Unit {
  out.write_char('{')
  for index, field in fields {
    if index > 0 {
      out.write_char(',')
    }
    json_append_string(out, field.0)
    out.write_char(':')
    out.write_string(field.1)
  }
  out.write_char('}')
}

///|
/// Encode a `Double` as a JSON number. Non-finite values have no JSON number
/// representation, so they become `null` instead of the bare tokens
/// `Infinity`/`NaN`, which would make the whole document unparseable.
pub fn json_encode_number(value : Double) -> String {
  let inf = 1.0 / 0.0
  // `value != value` is the portable NaN test: comparing against a bound NaN
  // constant is not reliable here, and `NaN` must never reach the wire.
  if value != value || value == inf || value == -inf {
    "null"
  } else {
    value.to_string()
  }
}
