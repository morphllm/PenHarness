#!/usr/bin/env bash
# K3.1 intelligence eval, one arm per run: regular K3.1 (prod morph-kimik3) vs abliterated (morph-kimik3-abliterated).
# Both arms: same NeMo-Skills benchmarks (AA classic-suite prompts) and Moonshot's sampling settings
# (temperature 1.0, top_p 0.95, server-default reasoning effort), through the arm's in-cluster rack1 router.
#
# usage: ./run_arm.sh abl|base [BENCHMARKS] [CONCURRENCY]
#   BENCHMARKS   NeMo-Skills list with repeats, default aime25:8,ifbench:1
#   CONCURRENCY  in-flight requests, default 32
#   env OUT=dir (default results/<arm>), EXTRA="++max_samples=3" for a smoke run
set -euo pipefail
ARM=${1:?abl|base}; BENCH=${2:-aime25:8,ifbench:1}; CONC=${3:-32}
case $ARM in
  abl)  NS=dynamo-kimik3abl SVC=kimik3abl-vllm-frontend MODEL=morph-kimik3-abliterated PORT=18600 ;;
  base) NS=dynamo-kimik3    SVC=kimik3-dynamo-frontend  MODEL=morph-kimik3             PORT=18700 ;;
  *) echo "arm must be abl or base" >&2; exit 2 ;;
esac
cd "$(dirname "$0")"
OUT=${OUT:-results/$ARM}

# kubectl port-forward drops on long runs; keep one alive for the whole eval.
( while true; do kubectl --context rack1 -n "$NS" port-forward "svc/$SVC" "$PORT:8000" >/dev/null 2>&1; sleep 2; done ) &
PF=$!
trap 'kill $PF 2>/dev/null; pkill -f "port-forward svc/$SVC $PORT:8000" 2>/dev/null || true' EXIT
until curl -sf -m 3 "localhost:$PORT/v1/models" >/dev/null; do sleep 1; done

. .venv/bin/activate
export OPENAI_API_KEY=unused HF_HOME=${HF_HOME:-$HOME/.cache/huggingface}
ns eval --server_type=openai --model="$MODEL" --server_address="http://localhost:$PORT/v1" \
  --benchmarks="$BENCH" --output_dir="$PWD/$OUT" \
  ++inference.temperature=1.0 ++inference.top_p=0.95 ++inference.tokens_to_generate=131072 \
  ++max_concurrent_requests="$CONC" ${EXTRA:-}
