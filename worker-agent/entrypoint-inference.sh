#!/usr/bin/env bash
# sgpu inference-agent entrypoint.
#
# Required env (set by `docker run -e ...` from the host bootstrap):
#   MODEL_REPO       Hugging Face repo (e.g. "Qwen/Qwen2.5-0.5B-Instruct")
#   MODEL_ID         our internal model id (UUID)
#   GATEWAY_URL      gateway base URL, no trailing slash
#   WORKER_TOKEN     short-lived HMAC checking in is allowed
#
# Optional:
#   PORT             default 8000
#   MAX_MODEL_LEN    default 8192
#   GPU_MEM_UTIL     default 0.9
#   EXTERNAL_IP      override; otherwise we curl ipify
set -euo pipefail

: "${MODEL_REPO:?MODEL_REPO required}"
: "${MODEL_ID:?MODEL_ID required}"
: "${GATEWAY_URL:?GATEWAY_URL required}"
: "${WORKER_TOKEN:?WORKER_TOKEN required}"
: "${PORT:=8000}"
: "${MAX_MODEL_LEN:=8192}"
: "${GPU_MEM_UTIL:=0.9}"

# Health watcher → ready-checkin. Backgrounded so we can exec vllm in fg.
(
  for i in $(seq 1 900); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      ip="${EXTERNAL_IP:-}"
      if [[ -z "$ip" ]]; then
        ip=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo "")
      fi
      if [[ -z "$ip" ]]; then
        echo "warning: no external IP detected; using internal" >&2
        ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
      fi
      echo "agent: vLLM healthy, checking in to ${GATEWAY_URL}"
      curl -fsS -X POST "${GATEWAY_URL}/inference/internal/ready-checkin" \
        -H "X-Worker-Token: ${WORKER_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"model_id\":\"${MODEL_ID}\",\"endpoint\":\"http://${ip}:${PORT}\"}" \
        || echo "agent: ready-checkin POST failed (gateway will reconcile)"
      exit 0
    fi
    sleep 1
  done
  echo "agent: vLLM did not become healthy in 900s" >&2
) &

# Hand off PID 1 to vllm so SIGTERM from `docker stop` reaches it cleanly.
exec vllm serve "$MODEL_REPO" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --download-dir /root/.cache/huggingface \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "$GPU_MEM_UTIL"
