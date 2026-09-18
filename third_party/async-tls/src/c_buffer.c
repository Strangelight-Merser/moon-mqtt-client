/*
 * Copyright 2025 International Digital Economy Academy
 * Copyright 2026 Strangelight-Merser
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <string.h>
#include <stdint.h>
#include <moonbit.h>

MOONBIT_FFI_EXPORT
void strangelight_async_tls_blit_to_c(
  char *dst,
  int32_t dst_offset,
  char *src,
  int32_t src_offset,
  int32_t len
) {
  memcpy(dst + dst_offset, src + src_offset, len);
}

MOONBIT_FFI_EXPORT
void strangelight_async_tls_blit_from_c(
  char *src,
  int32_t src_offset,
  char *dst,
  int32_t dst_offset,
  int32_t len
) {
  memcpy(dst + dst_offset, src + src_offset, len);
}

MOONBIT_FFI_EXPORT
int32_t strangelight_async_tls_pointer_is_null(void *ptr) {
  return ptr == 0;
}
