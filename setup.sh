#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo 'Error: this proof of concept targets Apple Silicon macOS' >&2; exit 1;
}
for tool in git clang++ python3 brew; do
  command -v "$tool" >/dev/null || { echo "Error: missing $tool" >&2; exit 1; }
done
xcode-select -p >/dev/null || { echo 'Error: install Xcode command-line tools' >&2; exit 1; }
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  brew install ffmpeg
fi
mkdir -p .deps .runtime/certs recordings
chmod 700 .runtime .runtime/certs
clang++ -std=c++17 -Wall -Wextra -Werror src/bonjour.cc -o .runtime/bonjour
TREE="$ROOT/.deps/openscreen"
if [[ ! -d "$TREE/.git" ]]; then
  git clone git@github.com:chromium/openscreen.git "$TREE"
  git -C "$TREE" checkout --detach a79edfcb0028e6f175a871b123b48b31989a7803
fi
export DEPOT_TOOLS_UPDATE=0
export PATH="$ROOT/.deps/depot_tools:$PATH"
if [[ ! -f "$ROOT/.runtime/dependencies-ready" ||
      ! -f "$TREE/build/config/BUILDCONFIG.gn" ||
      ! -x "$TREE/buildtools/mac/gn" ||
      ! -x "$TREE/third_party/ninja/ninja" ||
      ! -x "$TREE/third_party/cpython3/host/bin/python3" ]]; then
  if [[ ! -d "$ROOT/.deps/depot_tools/.git" ]]; then
    git clone --depth 1 https://chromium.googlesource.com/chromium/tools/depot_tools.git "$ROOT/.deps/depot_tools"
  fi
  if [[ ! -f "$ROOT/.deps/.gclient" ]]; then
    cat > "$ROOT/.deps/.gclient" <<'GCLIENT'
solutions = [{
  "name": "openscreen",
  "url": "git@github.com:chromium/openscreen.git",
  "managed": False,
  "custom_deps": {},
  "custom_vars": {"checkout_clang_coverage_tools": False},
}]
GCLIENT
  fi
  (cd "$ROOT/.deps" && gclient sync --no-history)
  touch "$ROOT/.runtime/dependencies-ready"
fi
python3 src/integrate.py
if [[ "$(xcode-select -p)" == */CommandLineTools ]]; then
  export LOCAL_CAST_CLT=1
  echo 'Building with the installed Command Line Tools macOS SDK'
fi
FFMPEG_PREFIX="$(brew --prefix ffmpeg)"
MAC_VERSION="$(sw_vers -productVersion | cut -d. -f1).0"
cd "$TREE"
buildtools/mac/gn gen out/Recorder --args="is_debug=false target_cpu=\"arm64\" have_ffmpeg=true have_libsdl2=false use_sysroot=false mac_deployment_target=\"$MAC_VERSION\" ffmpeg_include_dirs=[\"$FFMPEG_PREFIX/include\"] external_lib_dirs=[\"$FFMPEG_PREFIX/lib\"]"
third_party/ninja/ninja -C out/Recorder cast_receiver
cd "$ROOT/.runtime/certs"
umask 077
if [[ ! -f generated_root_cast_receiver.key && ! -f generated_root_cast_receiver.crt ]]; then
  "$TREE/out/Recorder/cast_receiver" -g
fi
[[ -s generated_root_cast_receiver.key && -s generated_root_cast_receiver.crt ]] || {
  echo 'Error: incomplete development credentials; inspect .runtime/certs' >&2; exit 1;
}
echo 'Setup complete. Run ./start.sh and launch the isolated Chrome with ./chrome.sh.'
echo 'Allow macOS local-network access and incoming connections when prompted.'
