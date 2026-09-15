#!/bin/sh
set -eu

channel=${1:-fixed}
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target=darwin-aarch64; expected_sha=6ddad0d536e85d22e23d69567211e9dbc6534fa9366cee5d276a37275917e07e ;;
  Linux:x86_64) target=linux-x86_64; expected_sha=9bbda7d342fa39654a65c23805e5ff5a3915a26b36627a2565a6b1a99d099e01 ;;
  *) echo "unsupported CI platform: $(uname -s) $(uname -m)" >&2; exit 2 ;;
esac

installer=$(mktemp "${TMPDIR:-/tmp}/moonbit-install.XXXXXX")
trap 'rm -f "$installer"' EXIT
curl -fsSL https://cli.moonbitlang.com/install/unix.sh -o "$installer"
moon_bin="${MOON_HOME:-$HOME/.moon}/bin/moon"

if [ "$channel" = latest ]; then
  bash "$installer" latest
  "$moon_bin" version --all
  exit
fi
if [ "$channel" != fixed ]; then
  echo "usage: $0 [fixed|latest]" >&2
  exit 2
fi

archive=$(mktemp "${TMPDIR:-/tmp}/moonbit-${target}.XXXXXX.tar.gz")
published=$(mktemp "${TMPDIR:-/tmp}/moonbit-${target}.XXXXXX.sha256")
trap 'rm -f "$installer" "$archive" "$published"' EXIT
base="https://cli.moonbitlang.com/binaries/latest/moonbit-${target}.tar.gz"
curl -fsSL "$base" -o "$archive"
curl -fsSL "$base.sha256" -o "$published"
published_sha=$(awk 'NR == 1 { print $1 }' "$published")
actual_sha=$(shasum -a 256 "$archive" | awk '{ print $1 }')
if [ "$published_sha" != "$expected_sha" ] || [ "$actual_sha" != "$expected_sha" ]; then
  echo "fixed MoonBit archive changed: expected=$expected_sha published=$published_sha actual=$actual_sha" >&2
  exit 1
fi

# The official installer supplies the matching core bundle. The archive hash
# check above makes this job fail closed when the rolling stable release moves.
bash "$installer" latest
first_line=$("$moon_bin" version --all | sed -n '1p')
case "$first_line" in
  "moon 0.1.20260904 (94521db 2026-09-04) "*) ;;
  *) echo "unexpected fixed MoonBit version: $first_line" >&2; exit 1 ;;
esac
"$moon_bin" version --all
