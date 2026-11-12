#!/bin/sh
# The box's inbound channel. Lead messages arrive either via the tmux2 mailbox (if the lead allows it)
# or via a dropped file (~/hunt/lead-msg.md, pushed by the lead over ssh). Both get PREPENDED to the
# work queue so a free worker picks the message up in seconds — never behind a running long task.
export PATH="$HOME/.local/bin:$PATH"
BOX=${BOX:-ph-hunt}
MODEL=${MODEL:-morph/morph-kimik3-fast}
PEER=${PEER:-MacBookPro.home.local}
cd "$HOME/hunt" || exit 1
touch mail.log queue.txt
while :; do
  msg=""
  if [ -s lead-msg.md ]; then
    msg=$(cat lead-msg.md)
    mv lead-msg.md "lead-msg.$(date +%s).done"
  fi
  if [ -z "$msg" ]; then
    tmux2 plugin-command mailbox "inbox $BOX" >/dev/null 2>&1
    m=$(tmux2 show-options -s -v "@mailbox_$BOX" 2>/dev/null)
    case "$m" in ''|'[]'|'{}') : ;; *) msg=$m ;; esac
  fi
  if [ -n "$msg" ]; then
    printf '%s\n' "$msg" >> mail.log
    one=$(printf '%s' "$msg" | tr '\n' ' ' | cut -c1-1500)
    flock queue.lock -c "printf 'LEAD STEER: %s\n' \"\$0\" | cat - queue.txt > queue.new && mv queue.new queue.txt" "$one" 2>/dev/null \
      || printf 'LEAD STEER: %s\n' "$one" >> queue.txt
    echo "$(date -u +%H:%M:%S) queued: $(printf '%s' "$one" | cut -c1-120)" >> mail.log
  fi
  sleep 8
done
