#!/usr/bin/env bash
# Terminal-Bench 4.0 on the abliterated model via Harbor (terminus-2 agent), through the rack1 in-cluster frontend.
# Regular K3.1 is NOT run here (use Moonshot's published 65.7). usage: ./tb_run.sh [N_TASKS] [N_CONCURRENT] [N_ATTEMPTS] [extra harbor args...]
set -euo pipefail
NS=dynamo-kimik3abl; SVC=kimik3abl-vllm-frontend; MODEL=morph-kimik3-abliterated; PORT=18600
cd "$(dirname "$0")"
( while true; do kubectl --context rack1 -n "$NS" port-forward "svc/$SVC" "$PORT:8000" >/dev/null 2>&1; sleep 2; done ) & PF=$!
trap 'kill $PF 2>/dev/null; pkill -f "port-forward svc/$SVC $PORT:8000" 2>/dev/null || true' EXIT
until curl -sf -m3 "localhost:$PORT/v1/models" >/dev/null; do sleep 1; done
. .venv-harbor/bin/activate
export OPENAI_BASE_URL="http://localhost:$PORT/v1" OPENAI_API_KEY=unused
NT="${1:-1}"; NC="${2:-1}"; NA="${3:-1}"; shift $(( $#<3 ? $# : 3 )) || true
harbor run -d terminal-bench/terminal-bench@4.0.0 -a terminus-2 -m "openai/$MODEL" \
  --n-tasks "$NT" --n-concurrent "$NC" --n-attempts "$NA" --job-name "tb4-abl-$(date +%H%M%S)" "$@"
