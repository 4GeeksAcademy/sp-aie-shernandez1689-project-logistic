#!/bin/bash
set -euo pipefail

website_pid=""
backoffice_pid=""

cleanup() {
    trap - EXIT
    if [ -n "$website_pid" ]; then
        kill "$website_pid" 2>/dev/null || true
    fi
    if [ -n "$backoffice_pid" ]; then
        kill "$backoffice_pid" 2>/dev/null || true
    fi
    wait || true
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

cd /app/website
node node_modules/next/dist/bin/next start --hostname 0.0.0.0 --port 3000 &
website_pid=$!

cd /app/backoffice
node node_modules/next/dist/bin/next start --hostname 0.0.0.0 --port 3001 &
backoffice_pid=$!

wait -n "$website_pid" "$backoffice_pid"