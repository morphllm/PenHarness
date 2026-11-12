#!/bin/sh
# Provisions each boat.dev sandbox for the hunt. Idempotent: tmux2, mailbox plugins, ssh rule,
# hunt skeleton, and opencode with its provider + permission-class. Every box runs this once.
set -eu
export PATH="$HOME/.local/bin:$PATH"
TAG=wasm-plugins-20261008-df4d91cb
LOG=$HOME/setup.log
exec >>"$LOG" 2>&1
echo "=== setup $(date -u) ==="

# 1. lead's ssh key into this box (so remote-attach + mailbox coordination work)
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
LEAD_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINyTP+GHUPLLGROl64C543BZC1wM2PzCgaqvdU51zu/D hunt-lead'
touch "$HOME/.ssh/authorized_keys"
grep -qF "$LEAD_KEY" "$HOME/.ssh/authorized_keys" || echo "$LEAD_KEY" >>"$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"

# 2. tmux2 from its matching release tag (never "latest" — it lags)
if [ ! -x "$HOME/.local/share/tmux2/bin/tmux" ]; then
  curl -fsSL "https://github.com/zackradisic/tmux/releases/download/$TAG/install-tmux2.sh" -o /tmp/it2.sh \
    || curl -fsSL "https://raw.githubusercontent.com/zackradisic/tmux/wasm-plugins/scripts/install-tmux2.sh" -o /tmp/it2.sh
  sh /tmp/it2.sh "$TAG" || echo "tmux2 install failed"
fi

# 3. ssh-exposed PATH (remote-attach links use non-interactive ssh)
grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$PATH"' >>"$HOME/.bashrc"
printf 'PATH=/home/user/.local/bin:/usr/local/bin:/usr/bin:/bin\n' > "$HOME/.ssh/environment"

# 4. hunt skeleton + the session the lead links to
mkdir -p "$HOME/hunt/evidence" "$HOME/hunt/findings.d" "$HOME/hunt/logs"
[ -f "$HOME/hunt/FINDINGS.md" ] || echo "# findings (not yet written)" >"$HOME/hunt/FINDINGS.md"
tmux2 new-session -d -s hunt -c "$HOME/hunt" 2>/dev/null || true

# 5. opencode config: base container's computer MCP + your provider + full permissions (headless
# auto-REJECT otherwise); keep whatever the box's base gave
python3 - <<'PY' || true
import json, os, os.path
p = os.path.expanduser("~/.config/opencode/opencode.json")
try:
    c = json.load(open(p))
except Exception:
    c = {}
c["provider"] = {"morph": {"npm": "@ai-sdk/openai-compatible", "name": "Morph",
  "options": {"baseURL": "https://api.morphllm.com/v1", "apiKey": "CHANGE_ME_OR_USE_GOBBLED_VIA_VAR"},
  "models": {"morph-glm53-744b": {"name": "glm53"}, "morph-dsv4flash": {"name": "dsv4flash"},
             "morph-kimik3": {"name": "kimik3"}, "morph-kimik3-fast": {"name": "kimik3-fast"}}}}
c["model"] = "morph/morph-kimik3-fast"
c["permission"] = {"edit":"allow","bash":"allow","webfetch":"allow","write":"allow","patch":"allow",
                   "list":"allow","grep":"allow","glob":"allow","read":"allow"}
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(c, open(p, "w"), indent=1)
PY

echo "=== setup done $(date -u) ==="
