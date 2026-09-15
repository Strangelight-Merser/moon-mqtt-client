#!/bin/sh
set -eu

channel=${1:-fixed}
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target=darwin-aarch64; expected_sha=6ddad0d536e85d22e23d69567211e9dbc6534fa9366cee5d276a37275917e07e ;;
  Linux:x86_64) target=linux-x86_64; expected_sha=9bbda7d342fa39654a65c23805e5ff5a3915a26b36627a2565a6b1a99d099e01 ;;
  *) echo "unsupported CI platform: $(uname -s) $(uname -m)" >&2; exit 2 ;;
esac

install_home="${MOON_HOME:-$HOME/.moon}"
moon_bin="$install_home/bin/moon"
download_dir=$(mktemp -d "${TMPDIR:-/tmp}/moonbit-install.XXXXXX")
trap 'rm -rf "$download_dir"' EXIT

if [ "$channel" = latest ]; then
  curl -fsSL https://cli.moonbitlang.com/install/unix.sh -o "$download_dir/install.sh"
  bash "$download_dir/install.sh" latest
  "$moon_bin" version --all
  exit
fi
if [ "$channel" != fixed ]; then
  echo "usage: $0 [fixed|latest]" >&2
  exit 2
fi

# The CDN version is the compiler version without the leading "v".
# Pin the compiler archive and its matching core bundle, not the latest alias.
version='0.10.12%2B1634b282e'
archive="$download_dir/moonbit.tar.gz"
published="$download_dir/moonbit.sha256"
core_archive="$download_dir/core.tar.gz"
expected_core_sha=784a12ce4e204a3a98a0b704a021f747b916412efacd4dfe2f4e5c27ae183ac1
base="https://cli.moonbitlang.com/binaries/$version/moonbit-${target}.tar.gz"
curl -fsSL "$base" -o "$archive"
curl -fsSL "$base.sha256" -o "$published"
published_sha=$(awk 'NR == 1 { print $1 }' "$published")
actual_sha=$(shasum -a 256 "$archive" | awk '{ print $1 }')
if [ "$published_sha" != "$expected_sha" ] || [ "$actual_sha" != "$expected_sha" ]; then
  echo "fixed MoonBit archive changed: expected=$expected_sha published=$published_sha actual=$actual_sha" >&2
  exit 1
fi

curl -fsSL "https://cli.moonbitlang.com/cores/core-$version.tar.gz" -o "$core_archive"
actual_core_sha=$(shasum -a 256 "$core_archive" | awk '{ print $1 }')
if [ "$actual_core_sha" != "$expected_core_sha" ]; then
  echo "fixed MoonBit core changed: expected=$expected_core_sha actual=$actual_core_sha" >&2
  exit 1
fi

# Install the bytes checked above. Refuse to mix them with an existing runtime.
if [ -d "$install_home" ] && [ -n "$(ls -A "$install_home")" ]; then
  echo "fixed installation requires an empty MOON_HOME: $install_home" >&2
  exit 1
fi
mkdir -p "$install_home/lib"
tar xf "$archive" -C "$install_home"
tar xf "$core_archive" -C "$install_home/lib"
chmod +x "$install_home"/bin/* "$install_home/bin/internal/tcc"
ln -sfn moon "$install_home/bin/moonx"
export MOON_HOME="$install_home"
export PATH="$install_home/bin:$PATH"

first_line=$("$moon_bin" version --all | sed -n '1p')
case "$first_line" in
  "moon 0.1.20260904 (94521db 2026-09-04) "*) ;;
  *) echo "unexpected fixed MoonBit version: $first_line" >&2; exit 1 ;;
esac
compiler_line=$("$install_home/bin/moonc" -v)
case "$compiler_line" in
  "v0.10.12+1634b282e (2026-09-07)") ;;
  *) echo "unexpected fixed compiler version: $compiler_line" >&2; exit 1 ;;
esac
"$moon_bin" -C "$install_home/lib/core" bundle --warn-list -a --all
"$moon_bin" -C "$install_home/lib/core" bundle --warn-list -a --target wasm-gc --quiet
"$moon_bin" version --all
