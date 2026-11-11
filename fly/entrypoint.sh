#!/usr/bin/env bash
# Prepare /work/hunt (harness files + findings dirs), then serve opencode on all
# interfaces with basic auth. OPENCODE_SERVER_PASSWORD is mandatory.
set -euo pipefail

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

mkdir -p findings.d lanes logs evidence
[ -f findings.d/index.jsonl ] || touch findings.d/index.jsonl

# Hunt loop: only lane/coordinator machines loop their root session on idle.
# Worker machines (one-shot sessions) never get the marker file.
if [ "${HUNT_LOOP:-0}" = "1" ]; then
    : > .hunt-loop
else
    rm -f .hunt-loop
fi

# Bind dual-stack: Fly's private 6PN network is IPv6-only, so an IPv4-only
# (0.0.0.0) listener is unreachable from wireguard peers and `fly proxy`.
exec opencode serve --hostname :: --port "${PORT:-4096}" "$@"
