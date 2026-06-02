#!/usr/bin/env bash
set -Eeuo pipefail

: "${PORT:=8000}"

shutdown() {
  trap - SIGTERM SIGINT
  kill -TERM "$api_pid" "$worker_pid" 2>/dev/null || true
  wait "$api_pid" "$worker_pid" 2>/dev/null || true
}

trap shutdown SIGTERM SIGINT

python -m arq app.queue.worker.WorkerSettings &
worker_pid=$!

python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
api_pid=$!

wait -n "$api_pid" "$worker_pid"
exit_code=$?

shutdown
exit "$exit_code"
