#!/usr/bin/env bash
set -euo pipefail

./start_openplc.sh &
openplc_pid=$!

cleanup() {
  kill "$openplc_pid" 2>/dev/null || true
  wait "$openplc_pid" 2>/dev/null || true
}
trap cleanup EXIT

if [[ -n "${SIMULATOR_HOST:-}" ]]; then
  for attempt in {1..60}; do
    if python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/", timeout=2)' >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$openplc_pid" 2>/dev/null; then
      echo "OpenPLC web server exited before becoming ready." >&2
      exit 1
    fi
    sleep 2
  done

  if ! python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/", timeout=2)' >/dev/null 2>&1; then
    echo "OpenPLC web server did not become ready within 120 seconds." >&2
    exit 1
  fi

  python3 /workdir/deploy_to_openplc.py \
    --plc http://127.0.0.1:8080 \
    --sim-host "$SIMULATOR_HOST" \
    --sim-port "${SIMULATOR_PORT:-5020}"

  for attempt in {1..60}; do
    if python3 -c 'import socket; socket.create_connection(("127.0.0.1", 502), timeout=2).close()' >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$openplc_pid" 2>/dev/null; then
      echo "OpenPLC web server exited before the Modbus runtime became ready." >&2
      exit 1
    fi
    sleep 2
  done

  if ! python3 -c 'import socket; socket.create_connection(("127.0.0.1", 502), timeout=2).close()' >/dev/null 2>&1; then
    echo "OpenPLC Modbus runtime did not listen on port 502 within 120 seconds." >&2
    exit 1
  fi
fi

wait "$openplc_pid"
