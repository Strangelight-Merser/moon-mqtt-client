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
