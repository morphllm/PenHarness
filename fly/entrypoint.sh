#!/usr/bin/env bash
# Prepare /work/hunt (harness files + findings dirs), then serve opencode on all
# interfaces with basic auth. OPENCODE_SERVER_PASSWORD is mandatory.
set -euo pipefail

if [ "${PENHARNESS_CONTROL_PLANE:-0}" = "1" ]; then
    exec python3 -m engagement.api
fi

if [ -z "${OPENCODE_SERVER_PASSWORD:-}" ]; then
    echo "OPENCODE_SERVER_PASSWORD is required (Machines API env or fly secret)" >&2
    exit 1
fi

mkdir -p /work/hunt
cd /work/hunt

# Seed the harness standing instructions if absent (idempotent; restart-safe).
cp -n /opt/harness/AGENTS.md /opt/harness/CLAUDE.md . 2>/dev/null || true
[ -d skills ] || cp -r /opt/harness/skills .
[ -d .opencode ] || cp -r /opt/harness/.opencode .
if [ -n "${HACK_ME_CONTROL_URL:-}" ] || [ "${HACK_ME_LOCAL_SUBAGENTS:-0}" = "1" ]; then
    cp /opt/harness/fleet.py ./fleet.py
    printenv HACK_ME_PAID_BRIEF_B64 | base64 --decode > brief.md
    printenv HACK_ME_PAID_SCOPE_B64 | base64 --decode > scope.md
fi

mkdir -p findings.d lanes logs evidence tasks

if [ -n "${HACK_ME_MODEL_BROKER_URL:-}" ]; then
    jq --arg url "$HACK_ME_MODEL_BROKER_URL" \
      '.provider.morph.options.baseURL=$url
       | .provider["morph-abliterated"].options.baseURL=$url
       | .provider.morph.options.apiKey="{env:HACK_ME_BROKER_TOKEN}"
       | .provider["morph-abliterated"].options.apiKey="{env:HACK_ME_BROKER_TOKEN}"' \
      /opt/harness/fly/opencode.json > /tmp/hack-me-opencode.json
    export OPENCODE_CONFIG=/tmp/hack-me-opencode.json
fi
[ -f findings.d/index.jsonl ] || touch findings.d/index.jsonl

# Hunt loop: only lane/coordinator machines loop their root session on idle.
# Worker machines (one-shot sessions) never get the marker file.
if [ "${HUNT_LOOP:-0}" = "1" ]; then
    if [ "${HACK_ME_LOCAL_SUBAGENTS:-0}" = "1" ]; then
        cat > .hunt-loop <<'EOF'
loop: Continue this authorized paid hunt as the master coordinator in THIS OpenCode instance. Use NATIVE SUBAGENTS, not remote fleet workers or a control bridge. Inspect findings.d/index.jsonl and dead-ends.md first. Every iteration:
1. Enumerate real HTTP/API routes, methods, content types, resource identities and authentication boundaries, and pin deployed stack versions from live evidence.
2. For EACH distinct hypothesis family or chain idea, spawn an independent native `kimi-lane` subagent (model morph/morph-kimik3): exact-version CVE reachability, first-party logical authorization/business bugs, and chain composition over every lane's confirmed primitives. Categories are not lanes: multiple hypotheses require multiple lanes. Each lane spawns its own native `deepseek-worker` subagents (model morph-abliterated/morph-dsv41flash-abliterated) in parallel for concrete probes. Do not use generic agents with an inherited model. Validate or falsify API behavior rather than inferring server behavior from client code alone.
3. Serialize every new primitive, finding and falsification to findings.d markdown plus index.jsonl with request/response proof and a harmless control. Feed each confirmed primitive into more recon, logical probes and chain composition. Run independent lanes and child workers in parallel.
Never run setup, ask an operator question, use fleet.py, call Fly APIs or use another provider. The coordinator alone merges the final ledger.
Before completion, create a native chains lane if one does not exist, have it review every lane's ledger, and gather its verified candidate chains or its explicit falsification of each candidate. Independently verify every high/critical claim and distinguish offline mechanics from live server impact. A chain can use only confirmed primitives; raw hopes are not chains.
Do not call the hunt saturated merely because time elapsed, money was spent, one finding looks important, or several loop iterations finished. Only write {"reason":"saturated"} to .hunt-complete.json when every feasible next hypothesis is falsified or blocked with a recorded reason and the chains/review step is finished. Then assemble the final ledger, touch .hunt-loop-pause and stop. On budget, authorization, deadline, or cancellation refusal, stop immediately without retrying.
EOF
    elif [ -n "${HACK_ME_CONTROL_URL:-}" ]; then
        cat > .hunt-loop <<'EOF'
loop: Continue this authorized paid hunt as the master coordinator. Inspect findings.d/index.jsonl and dead-ends.md before choosing work. Every iteration has these stages:
1. Enumerate the real HTTP/API surface: routes, methods, content types, resource identities and authentication boundaries. Pin the actually deployed stack and exact versions from live evidence.
2. For EACH distinct hypothesis family or chain idea, create a separate Kimi K3 lane in parallel: exact-version CVE/advisory reachability, first-party logical authorization/business bugs, and chain composition using every relevant lane's confirmed ledger. One category can have many independent lanes. EACH lane-head directly writes task files and dispatches its own DeepSeek fan-out through the bridge; the coordinator creates and steers lanes rather than acting as the only dispatcher. Validate or falsify API behavior rather than inferring server behavior from client code alone.
3. Serialize every new primitive, finding and falsification to findings.d markdown plus index.jsonl with request/response proof and a harmless control. Feed each confirmed primitive back into another iteration of recon, logical probing and chain composition.
Each lane uses canonical fleet.py dispatch/wait/harvest/status commands for its workers; the coordinator uses lane/steer/wait/status/harvest to manage the idea lanes. Enumerate several high-value safe actions and do them in parallel rather than one; preserve dedicated DeepSeek dispatch workers. Never run up/down, run interactive setup, ask an operator question, or use another provider.
Before completion, create a chains lane if one does not exist, have it read every lane's ledger, and gather its verified candidate chains or its explicit falsification of each candidate. Verify every high/critical claim independently and distinguish offline mechanics from live server impact. A chain can use only confirmed primitives; raw hopes are not chains.
Do not call the hunt saturated merely because time elapsed, money was spent, one finding looks important, or several loop iterations finished. Only write {"reason":"saturated"} to .hunt-complete.json when every feasible next hypothesis is falsified or blocked, each reason is recorded, and the chains/review step is finished. Then run python3 fleet.py harvest, touch .hunt-loop-pause and stop. On budget, authorization, deadline, or cancellation refusal, stop immediately without retrying.
EOF
    else
        : > .hunt-loop
    fi
else
    rm -f .hunt-loop
fi

# Bind explicitly to the current private fabric address where Fly provides it.
# Do not substitute an IPv4-only listener or fall back to public ingress.
listen_hostname="${OPENCODE_HOSTNAME:-}"
if [ -z "$listen_hostname" ] && [ -n "${FLY_PRIVATE_IP:-}" ]; then
    listen_hostname=fly-local-6pn
fi
exec opencode serve --hostname "${listen_hostname:-::}" --port "${PORT:-4096}" "$@"
