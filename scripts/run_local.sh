#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR:$PYTHONPATH"
cd "$ROOT_DIR"

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "uvicorn not found. Did you install requirements?" >&2
  exit 1
fi

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
API_PID=$!

echo "API запущен (PID $API_PID)"

python worker/worker.py &
WORKER_PID=$!

echo "Worker запущен (PID $WORKER_PID)"

BOT_PID=""
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  python bot/bot.py &
  BOT_PID=$!
  echo "Bot запущен (PID $BOT_PID)"
else
  echo "TELEGRAM_BOT_TOKEN не задан — бот не будет запущен"
fi

cleanup() {
  echo "Останавливаю процессы..."
  kill "$API_PID" "$WORKER_PID" ${BOT_PID:+$BOT_PID} 2>/dev/null || true
  wait "$API_PID" "$WORKER_PID" ${BOT_PID:+$BOT_PID} 2>/dev/null || true
}

trap cleanup EXIT

wait
