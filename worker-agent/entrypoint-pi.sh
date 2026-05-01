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

# Tailscale: when TS_AUTHKEY is set, join the user's tailnet so we can
# reach gateway-side Redis proxy via MagicDNS. Skipped when unset (e.g.
# bare-metal hosts that already run tailscaled, or local dev).
if [ -n "${TS_AUTHKEY:-}" ]; then
  echo "[entrypoint] starting tailscaled (kernel mode)"
  mkdir -p /var/run/tailscale /var/lib/tailscale
  tailscaled --state=/var/lib/tailscale/tailscaled.state \
             --socket=/var/run/tailscale/tailscaled.sock &
  for i in $(seq 1 30); do
    [ -S /var/run/tailscale/tailscaled.sock ] && break
    sleep 1
  done
  echo "[entrypoint] tailscale up (hostname=${MACHINE_ID}, ephemeral)"
  tailscale up --auth-key="${TS_AUTHKEY}" \
               --hostname="${MACHINE_ID}" \
               --ephemeral=true \
               --accept-dns=true \
               --reset
  echo "[entrypoint] tailnet status:"
  tailscale status | head -5 || true
fi

VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"
WORKER_LOG_PATH="${WORKER_LOG_PATH:-/var/log/vllm.log}"
mkdir -p "$(dirname "$WORKER_LOG_PATH")"
: > "$WORKER_LOG_PATH"
export WORKER_LOG_PATH

echo "[entrypoint] starting vllm: model=$MODEL_ID served-as=$APP_ID port=$VLLM_PORT extra=$VLLM_EXTRA_ARGS log=$WORKER_LOG_PATH"
# stdbuf forces line-buffered output so the worker-agent's log shipper sees
# vllm output as it's produced instead of in 4-KB stdio chunks. Output goes
# straight to the log file (the agent tails it); we also background a tail
# so anyone attached to the container can still see vllm's stdout.
stdbuf -oL -eL python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL_ID" \
  --served-model-name "$APP_ID" \
  --port "$VLLM_PORT" \
  $VLLM_EXTRA_ARGS \
  > "$WORKER_LOG_PATH" 2>&1 &
VLLM_PID=$!
tail -F "$WORKER_LOG_PATH" &
TAIL_PID=$!

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

# Worker-agent runs in foreground; if it exits, kill vllm + tail too.
trap 'kill -TERM "$VLLM_PID" "$TAIL_PID" 2>/dev/null || true' EXIT

echo "[entrypoint] starting worker-agent: app=$APP_ID machine=$MACHINE_ID"
exec python3 -m worker_agent.main
