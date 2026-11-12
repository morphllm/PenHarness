#!/bin/bash
# Lead → box: push a lead-message (file drop) AND grant each box permission to deliver into your
# mailbox boxes, then try to wake the mailbox box. usage: ./tell.sh <stream|all> "<message>"
set -u
export PATH="$HOME/.local/bin:$PATH"
KEY=$HOME/.ssh/boat_hunt_ed25519
PEER=$(cat "$HOME/.local/share/tmux2/peer-name" 2>/dev/null || hostname)
TARGETS="$1"; shift
if [ "${1:-}" = "-f" ]; then MSG=$(cat "$2"); else MSG="$*"; fi

send_one() {
  local s=$1 box=ph-$1
  printf '%s\n' "$MSG" > "/tmp/lead-msg-$s.md"
  scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -i "$KEY" "/tmp/lead-msg-$s.md" "$box:~/hunt/lead-msg.md" || { echo "$s: scp failed"; return; }
  ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10 -i "$KEY" "$box" \
    "export PATH=\$HOME/.local/bin:\$PATH; tmux2 plugin-peers allow $PEER mailbox >/dev/null 2>&1; echo ok" >/dev/null 2>&1
  echo "$s: delivered"
}

if [ "$TARGETS" = all ]; then
  for s in recon cve auth query cdp ingest flags replay ai oss edge chain surface surveys errortrack warehouse content billing mcp ratelimit accounts idor timing quilt jobs; do send_one "$s" & done
  wait
else
  for s in $TARGETS; do send_one "$s" & done
  wait
fi
