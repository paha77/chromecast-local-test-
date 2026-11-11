#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
LOCK="$PWD/.runtime/receiver.lock"
[[ -f "$LOCK/pid" ]] || { echo 'Receiver is not running'; exit 0; }
read -r pid < "$LOCK/pid"
case "$pid" in ''|*[!0-9]*) echo 'Error: invalid receiver PID' >&2; exit 1;; esac
command_line="$(ps -p "$pid" -o command= || true)"
[[ "$command_line" == *"$PWD/src/run.py"* || "$command_line" == *start.sh* ]] || {
  echo 'Error: stale receiver lock; inspect .runtime/receiver.lock before removing it' >&2; exit 1;
}
echo 'Stopping receiver; waiting for MP4 finalization...'
kill -TERM "$pid"
# Final encoding can take longer than real time. Do not SIGKILL FFmpeg.
while kill -0 "$pid" 2>/dev/null && [[ -d "$LOCK" ]]; do sleep 1; done
[[ ! -d "$LOCK" ]] || { echo 'Error: shutdown interrupted; inspect recordings/ and receiver lock' >&2; exit 1; }
echo 'Receiver stopped'
