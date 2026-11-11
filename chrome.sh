#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
CERT="$PWD/.runtime/certs/generated_root_cast_receiver.crt"
CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
[[ -f "$CERT" ]] || { echo 'Error: run ./setup.sh first' >&2; exit 1; }
[[ -x "$CHROME" ]] || { echo 'Error: Google Chrome not found in /Applications' >&2; exit 1; }
echo "Cast diagnostic log: $PWD/.runtime/chrome-cast.log"
echo 'Close the isolated Chrome before relaunching so these flags take effect.'
EXTRA_FLAGS=()
if [[ "${CAST_INTERFACE:-lo0}" == lo0 ]]; then
  echo 'Local Cast receiver: look for Local Recorder or Receiver-127_0_0_1 in the Cast menu.'
  EXTRA_FLAGS+=(--enable-features=CastAllowAllIPs --media-router-cast-device-ips=127.0.0.1:8010)
fi
exec "$CHROME" --user-data-dir="$PWD/.runtime/chrome-profile" \
  --no-first-run --no-default-browser-check \
  --enable-logging --log-file="$PWD/.runtime/chrome-cast.log" \
  --vmodule=cast_socket=2,cast_auth_util=2,cast_trust_store=2,cast_cert_reader=2,cast_cert_validator=3 \
  --cast-log-device-cert-chain \
  --cast-developer-certificate-path="$CERT" "${EXTRA_FLAGS[@]}" "$@"
