///|
test "frigate emits once only for completed person in target zone" {
  let d = AlertDeduper::new(8)
  let base = FrigateEvent::{
    id: "evt-7",
    phase: New,
    label: "person",
    zones: ["front_yard"],
  }
  assert_eq(d.accept(base, "front_yard"), None)
  assert_eq(d.accept({ ..base, phase: Update, }, "front_yard"), None)
  assert_eq(
    d.accept({ ..base, phase: End, }, "front_yard"),
    Some({ event_id: "evt-7", text: "person completed in front_yard", }),
  )
  assert_eq(d.accept({ ..base, phase: End, }, "front_yard"), None)
}

///|
test "frigate filters label and zone and bounds dedup state" {
  let d = AlertDeduper::new(2)
  assert_eq(
    d.accept(
      { id: "cat", phase: End, label: "cat", zones: ["porch"], },
      "porch",
    ),
    None,
  )
  assert_eq(
    d.accept(
      { id: "yard", phase: End, label: "person", zones: ["yard"], },
      "porch",
    ),
    None,
  )
  ignore(
    d.accept(
      { id: "a", phase: End, label: "person", zones: ["porch"], },
      "porch",
    ),
  )
  ignore(
    d.accept(
      { id: "b", phase: End, label: "person", zones: ["porch"], },
      "porch",
    ),
  )
  ignore(
    d.accept(
      { id: "c", phase: End, label: "person", zones: ["porch"], },
      "porch",
    ),
  )
  assert_eq(d.remembered(), 2)
  assert_true(
    d.accept(
      { id: "a", phase: End, label: "person", zones: ["porch"], },
      "porch",
    )
    is Some(_),
  )
}

///|
/// Parse or abort the test: an alert payload must always be JSON.
fn alert_json(alert : Alert) -> Map[String, Json] {
  let payload = alert.to_json()
  let value = @json.parse(payload) catch { _ => abort("alert payload is not JSON") }
  match value {
    Object(object) => object
    _ => abort("alert payload must be a JSON object")
  }
}

///|
test "frigate alerts are JSON with escaped strings" {
  let alert : Alert = {
    event_id: "evt-\"7\"\\a",
    text: "person completed in front\nyard",
  }
  let object = alert_json(alert)
  match object.get("event_id") {
    Some(String(value)) => assert_eq(value, "evt-\"7\"\\a")
    _ => abort("alert lost the event id")
  }
  match object.get("text") {
    Some(String(value)) => assert_eq(value, "person completed in front\nyard")
    _ => abort("alert lost the text")
  }
}

///|
test "frigate alert payload round trips control characters" {
  let alert : Alert = { event_id: "evt\u{02}7", text: "done\u{1F}" }
  let payload = alert.to_json()
  assert_eq(payload, "{\"event_id\":\"evt\\u00027\",\"text\":\"done\\u001f\"}")
  let object = alert_json(alert)
  match object.get("event_id") {
    Some(String(value)) => assert_eq(value, "evt\u{02}7")
    _ => abort("alert lost the control-character event id")
  }
  match object.get("text") {
    Some(String(value)) => assert_eq(value, "done\u{1F}")
    _ => abort("alert lost the control-character text")
  }
}
