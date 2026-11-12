#!/bin/sh
# Lead-side mailbox reader: drains box "zack" into a local log so no report is lost.
export PATH="$HOME/.local/bin:$PATH"
OUT=${1:-/tmp/hunt-inbox.log}
touch "$OUT"
while :; do
  tmux2 plugin-command mailbox "inbox zack" >/dev/null 2>&1
  m=$(tmux2 show-options -s -v @mailbox_zack 2>/dev/null)
  case "$m" in
    ''|'[]'|'{}') : ;;
    *) printf '%s %s\n' "$(date -u +%H:%M:%S)" "$m" >>"$OUT" ;;
  esac
  sleep 8
done
