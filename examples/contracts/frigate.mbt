///|
pub(all) enum EventPhase {
  New
  Update
  End
} derive(Debug, Eq)

///|
pub(all) struct FrigateEvent {
  id : String
  phase : EventPhase
  label : String
  zones : Array[String]
} derive(Debug, Eq)

///|
pub(all) struct Alert {
  event_id : String
  text : String
} derive(Debug, Eq)

///|
/// Serialize the alert to the published JSON payload. Every string field is
/// escaped, so an event id or zone containing a quote, a backslash or a control
/// character cannot produce malformed JSON.
pub fn Alert::to_json(self : Alert) -> String {
  let out = StringBuilder()
  json_append_object(out, [
    ("event_id", json_encode_string(self.event_id)),
    ("text", json_encode_string(self.text)),
  ])
  out.to_string()
}

///|
/// A bounded replay-deduplication window. Once more than `capacity` different
/// alerts have been emitted, the oldest id can be emitted again if replayed.
pub struct AlertDeduper {
  recent : Array[String?]
  mut next : Int
  mut count : Int
}

///|
pub fn AlertDeduper::new(capacity : Int) -> AlertDeduper {
  let safe_capacity = if capacity < 1 { 1 } else { capacity }
  { recent: Array::make(safe_capacity, None), next: 0, count: 0, }
}

///|
fn string_array_contains(xs : Array[String], wanted : String) -> Bool {
  for x in xs {
    if x == wanted {
      return true
    }
  }
  false
}

///|
fn AlertDeduper::seen(self : AlertDeduper, id : String) -> Bool {
  for slot in self.recent {
    match slot {
      Some(old) if old == id => return true
      _ => ()
    }
  }
  false
}

///|
/// Emit exactly one alert per id for completed person events in the target
/// zone. New/update events, other labels and other zones are ignored.
pub fn AlertDeduper::accept(
  self : AlertDeduper,
  event : FrigateEvent,
  target_zone : String,
) -> Alert? {
  if event.phase != End ||
    event.label != "person" ||
    !string_array_contains(event.zones, target_zone) ||
    self.seen(event.id) {
    return None
  }
  self.recent[self.next] = Some(event.id)
  self.next = (self.next + 1) % self.recent.length()
  if self.count < self.recent.length() {
    self.count += 1
  }
  Some({ event_id: event.id, text: "person completed in \{target_zone}", })
}

///|
pub fn AlertDeduper::remembered(self : AlertDeduper) -> Int {
  self.count
}
