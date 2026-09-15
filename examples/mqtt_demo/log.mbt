///|
/// Unbuffered diagnostics line for the demo processes.
///
/// `println` goes to the C stdout buffer, which is block-buffered when stdout is
/// a pipe. That hides the demo's evidence from a parent process or a log
/// collector until the buffer fills, so every demo line is written and flushed
/// explicitly. The test harness reads these lines as they happen.
pub async fn log_line(line : String) -> Unit {
  // `@stdio.stdout` writes through without the C stdio block buffer that
  // `println` uses, so a parent process sees every line immediately.
  @stdio.stdout.write(@utf8.encode(line + "\n"))
}
