#!/bin/bash
# Launch one PostHog hunt box end-to-end: create sandbox, link over ssh + remote-attach, push
# brief + worker + mailbox hooks + creds, start 3 workers + the steer reader.
# usage: ./launch.sh <stream> [type]
set -uo pipefail
STREAM=${1:?stream}
TYPE=${2:-default}
BASE="$(cd "$(dirname "$0")/.." && pwd)"
KEY=$HOME/.ssh/boat_hunt_ed25519
HOST=ph-$STREAM
PEER=$(cat "$HOME/.local/share/tmux2/peer-name" 2>/dev/null || hostname)
export PATH="$HOME/.local/bin:$PATH"

say() { echo "[$STREAM $(date +%H:%M:%S)] $*"; }

if ssh -o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=no -i "$KEY" "$HOST" true 2>/dev/null; then
  say "box already reachable, reusing"
else
  OUT=$(boat new --type "$TYPE" --ttl 28800 --no-update --json --setup-file "$BASE/setup.sh" 2>&1)
  ID=$(printf '%s\n' "$OUT" | grep -o '"id":"bx_[a-z0-9]*"' | head -1 | cut -d'"' -f4)
  [ -n "$ID" ] || { say "CREATE FAILED: $OUT"; exit 1; }
  say "created $ID"
  for i in $(seq 1 60); do
    EP=$(boat info "$ID" 2>/dev/null | jq -r '.sandbox.sshEndpoint // empty')
    [ -n "$EP" ] && break
    sleep 2
  done
  [ -n "$EP" ] || { say "no ssh endpoint"; exit 1; }
  IP=${EP%:*}; PORT=${EP#*:}
  say "endpoint $IP:$PORT"

  python3 - "$HOST" "$IP" "$PORT" "$KEY" <<'PY'
import sys, pathlib
host, ip, port, key = sys.argv[1:5]
p = pathlib.Path.home() / ".ssh" / "config"
txt = p.read_text()
if f"Host {host}\n" not in txt:
    p.write_text(txt.rstrip("\n") + f"""

Host {host}
  HostName {ip}
  Port {port}
  User user
  IdentityFile {key}
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
  ServerAliveInterval 30

""")
    print("wrote", host)
PY
  for i in $(seq 1 90); do
    if ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no -i "$KEY" "$HOST" \
        'test -x $HOME/.local/share/tmux2/bin/tmux' 2>/dev/null; then break; fi
    sleep 4
  done
  say "tmux2 ready"
fi

# push everything the box needs
cat "$BASE/../whitehat-common.md" "$BASE/../streams/$STREAM.md" 2>/dev/null > "$BASE/brief-$STREAM.md" || \
cat "$BASE/../common.md" "$BASE/../streams/$STREAM.md" > "$BASE/brief-$STREAM.md"
scp -q -o StrictHostKeyChecking=no -i "$KEY" "$BASE/brief-$STREAM.md" "$HOST:~/hunt/brief.md" || { say "brief push failed"; exit 1; }
scp -q -o StrictHostKeyChecking=no -i "$KEY" "$BASE/worker.sh" "$BASE/mailbox.sh" "$HOST:~/hunt/"
[ -f "$BASE/../creds.json" ] && scp -q -o StrictHostKeyChecking=no -i "$KEY" "$BASE/../creds.json" "$HOST:~/hunt/creds.json"
[ -f "$BASE/queue-$STREAM.txt" ] && scp -q -o StrictHostKeyChecking=no -i "$KEY" "$BASE/queue-$STREAM.txt" "$HOST:~/hunt/queue.txt"
say "brief pushed"

# link + let the box write into our mailbox
tmux2 remote-attach -t hunt "$HOST" >/dev/null 2>&1 &
sleep 8
tmux2 plugin-peers allow "$HOST" mailbox >/dev/null 2>&1

# start the workers and the steer reader
ssh -o StrictHostKeyChecking=no -i "$KEY" "$HOST" \
  "cd ~/hunt && chmod +x worker.sh mailbox.sh; mkdir -p findings.d logs claims
   pkill -f '[m]ailbox.sh' 2>/dev/null; pkill -f '[w]orker.sh' 2>/dev/null; sleep 2
   rm -f .done
   i=1; while [ '\$i' -le 3 ]; do W=w\$i nohup sh worker.sh >/dev/null 2>&1 </dev/null & i=\$((i+1)); done
   BOX=$HOST PEER=$PEER nohup sh mailbox.sh >/dev/null 2>&1 </dev/null &
   sleep 2; echo ok"
say "workers+reader started"
