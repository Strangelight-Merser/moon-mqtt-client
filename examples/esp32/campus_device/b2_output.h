#pragma once
// Deliberately disabled. Set all three values only after board/pin/wiring review.
#ifndef B2_OUTPUT_PIN
#define B2_OUTPUT_PIN -1
#endif
#ifndef B2_APPLY_PULSE_PIN
#define B2_APPLY_PULSE_PIN -1
#endif
#ifndef B2_WIRING_VERIFIED
#define B2_WIRING_VERIFIED 0
#endif
static bool b2Setup() {
  if (B2_OUTPUT_PIN < 0 && B2_APPLY_PULSE_PIN < 0) return true;
  if (!B2_WIRING_VERIFIED || B2_OUTPUT_PIN == B2_APPLY_PULSE_PIN ||
      !GPIO_IS_VALID_OUTPUT_GPIO(B2_OUTPUT_PIN) ||
      !GPIO_IS_VALID_OUTPUT_GPIO(B2_APPLY_PULSE_PIN)) return false;
  digitalWrite(B2_OUTPUT_PIN, LOW); pinMode(B2_OUTPUT_PIN, OUTPUT);
  digitalWrite(B2_APPLY_PULSE_PIN, LOW); pinMode(B2_APPLY_PULSE_PIN, OUTPUT);
  return true;
}
static void b2Apply(bool on) {
  if (B2_OUTPUT_PIN < 0) return;
  digitalWrite(B2_OUTPUT_PIN, on ? HIGH : LOW);
  // A separate externally observed pulse distinguishes two applies even when
  // both request the same absolute state. The MCU's own count is not evidence.
  digitalWrite(B2_APPLY_PULSE_PIN, HIGH); delayMicroseconds(1000);
  digitalWrite(B2_APPLY_PULSE_PIN, LOW);
}
