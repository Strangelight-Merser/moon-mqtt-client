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
/// Opaque pointer into a C buffer. Copied from moonbitlang/async internal
/// `c_buffer` so this module does not import official internal packages.
/// C exports use the `strangelight_async_tls_` prefix.
#external
priv type CBuffer

///|
#borrow(src)
extern "C" fn CBuffer::blit_from_bytes(
  dst : CBuffer,
  dst_offset? : Int = 0,
  src~ : Bytes,
  src_offset? : Int = 0,
  len~ : Int,
) = "strangelight_async_tls_blit_to_c"

///|
#borrow(dst)
extern "C" fn CBuffer::blit_to_bytes(
  src : CBuffer,
  src_offset? : Int = 0,
  dst~ : FixedArray[Byte],
  dst_offset? : Int = 0,
  len~ : Int,
) = "strangelight_async_tls_blit_from_c"

///|
extern "C" fn CBuffer::is_null(ptr : CBuffer) -> Bool = "strangelight_async_tls_pointer_is_null"
