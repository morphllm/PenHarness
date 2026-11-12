#!/bin/bash
# Append task lines to a box's work queue (ssc a file, then an atomic append on the box).
# usage: ./enqueue.sh <stream> -f file.txt
set -u
S=${1:?stream}
[ "${2:-}" = "-f" ] && FILE=$3 || FILE=$2
BOX=ph-$S
scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -i ~/.ssh/boat_hunt_ed25519 "$FILE" "$BOX:~/hunt/.queue-incoming" \
  || { echo "$S: scp failed"; exit 1; }
ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=12 -i ~/.ssh/boat_hunt_ed25519 "$BOX" \
  'cd ~/hunt && touch queue.txt && flock queue.lock -c "cat .queue-incoming >> queue.txt" && rm .queue-incoming && printf "%s: q=%s\n" "'"$S"'" "$(wc -l < queue.txt)"'
