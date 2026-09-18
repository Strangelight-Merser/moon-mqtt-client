///|
test "default reconnect initialization does not collapse supplied entropy" {
  let streams = Array::makei(16, index => {
    ReconnectBackoff::new_default_with(
      Some(Bytes::make(4, (index + 1).to_byte())),
      1700000000000UL,
      (index + 1).to_uint64(),
    )
  })
  let first = streams[0].seed
  let mut all_same = true
  for index in 1..<streams.length() {
    if streams[index].seed != first {
      all_same = false
    }
  }
  assert_false(all_same)
}

///|
test "default reconnect fallback separates a fixed-time construction burst" {
  let now = 1700000000000UL
  let first = ReconnectBackoff::new_default_with(None, now, 1UL)
  let second = ReconnectBackoff::new_default_with(None, now, 2UL)
  assert_true(first.seed != second.seed)
}

///|
test "explicit reconnect seeds are reproducible and zero is safe" {
  let left = ReconnectBackoff::new(123456789)
  let right = ReconnectBackoff::new(123456789)
  for _ in 0..<32 {
    assert_eq(left.next(), right.next())
  }

  let zero = ReconnectBackoff::new(0)
  assert_true(zero.seed != 0)
  for _ in 0..<32 {
    ignore(zero.next())
    assert_true(zero.seed != 0)
  }
}

///|
test "reconnect jitter keeps every wait inside its delay window" {
  for delay in [1, 250, 5000] {
    let backoff = ReconnectBackoff::new(-1)
    for _ in 0..<128 {
      let jitter = backoff.next() % delay
      let wait = delay + jitter
      assert_true(jitter >= 0 && jitter < delay)
      assert_true(wait >= delay && wait < delay * 2)
    }
  }
}
