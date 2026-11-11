#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
BIN="$ROOT/.deps/openscreen/out/Recorder/cast_receiver"
[[ -x "$BIN" && -x .runtime/bonjour && -f .runtime/certs/generated_root_cast_receiver.crt ]] || {
  echo 'Error: run ./setup.sh first' >&2; exit 1;
}
mkdir -p .runtime recordings
if ! mkdir .runtime/receiver.lock 2>/dev/null; then
  echo 'Error: receiver already running or stale .runtime/receiver.lock (see README)' >&2
  exit 1
fi
trap 'rm -f .runtime/receiver.lock/pid; rmdir .runtime/receiver.lock' EXIT
echo $$ > .runtime/receiver.lock/pid
listeners="$(/usr/sbin/lsof -nP -iTCP:8010 -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "$listeners" ]]; then
  echo 'Error: TCP port 8010 is already in use. Stop the existing receiver before starting another.' >&2
  echo "$listeners" >&2
  exit 1
fi
INTERFACE="${CAST_INTERFACE:-lo0}"
exec python3 "$ROOT/src/run.py" "$INTERFACE"
