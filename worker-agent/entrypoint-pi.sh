#!/usr/bin/env bash
# Entrypoint for the PI custom-template worker image.
#
# Two processes run in the same container:
#   - vllm OpenAI-compatible server on $VLLM_PORT (background)
#   - worker-agent (foreground; container exits when it does)
#
# worker-agent waits for vllm to be /health-ready before BRPOPping the queue.
set -euo pipefail

: "${MODEL_ID:?MODEL_ID env var required}"
: "${APP_ID:?APP_ID env var required}"
: "${MACHINE_ID:?MACHINE_ID env var required}"
: "${GATEWAY_URL:?GATEWAY_URL env var required}"
: "${REGISTRATION_TOKEN:?REGISTRATION_TOKEN env var required}"

VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

echo "[entrypoint] starting vllm: model=$MODEL_ID port=$VLLM_PORT extra=$VLLM_EXTRA_ARGS"
python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_ID" \
  --port "$VLLM_PORT" \
  $VLLM_EXTRA_ARGS \
  &
VLLM_PID=$!

# Wait up to 10 minutes for vllm to be ready (large models take a while)
echo "[entrypoint] waiting for vllm /health on :$VLLM_PORT ..."
for i in $(seq 1 600); do
  if curl -sf "http://127.0.0.1:$VLLM_PORT/health" > /dev/null 2>&1; then
    echo "[entrypoint] vllm ready after ${i}s"
    break
  fi
  if ! kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "[entrypoint] vllm died during startup" >&2
    exit 1
  fi
  sleep 1
done

# Worker-agent runs in foreground; if it exits, kill vllm too.
trap 'kill -TERM "$VLLM_PID" 2>/dev/null || true' EXIT

echo "[entrypoint] starting worker-agent: app=$APP_ID machine=$MACHINE_ID"
exec python3 -m worker_agent.main
