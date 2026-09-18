// Copyright 2025 International Digital Economy Academy
// Copyright 2026 Strangelight-Merser
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

///|
/// Same layout as moonbitlang/async internal `io_buffer.Buffer` and therefore
/// as `@io.ReaderBuffer`. Copied so this module does not import internals.
priv struct IoBuffer {
  mut buf : FixedArray[Byte]
  mut start : Int
  mut len : Int
}

///|
fn IoBuffer::new(size : Int) -> IoBuffer {
  { buf: FixedArray::make(size, 0), start: 0, len: 0 }
}

///|
const SEGMENT_SIZE = 1024

///|
fn IoBuffer::enlarge_to(self : IoBuffer, n : Int) -> Unit {
  let capacity = self.buf.length()
  if self.start + n <= capacity {
    return
  }
  if n <= capacity {
    self.buf.blit_to(self.buf, src_offset=self.start, len=self.len)
    self.start = 0
    return
  }
  let padding = n % SEGMENT_SIZE
  let target_len = if padding == 0 { n } else { n - padding + SEGMENT_SIZE }
  let new_buf = FixedArray::make(target_len, b'0')
  self.buf.blit_to(new_buf, src_offset=self.start, dst_offset=0, len=self.len)
  self.buf = new_buf
  self.start = 0
}

///|
fn IoBuffer::drop(self : IoBuffer, n : Int) -> Unit {
  guard! n <= self.len
  self.len -= n
  if self.len == 0 {
    self.start = 0
  } else {
    self.start += n
  }
}
