#!/bin/sh
# One work unit per loop iteration: pop a task off a flock'ed queue, run a FRESH opencode session on it, and
# have the agent write its findings as *files* (never stdout stdout) — index.jsonl + one .md per task.
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/hunt" || exit 1
MODEL=${MODEL:-morph/morph-kimik3-fast}
W=${W:-w1}
mkdir -p findings.d claims logs
touch queue.txt
while :; do
  task=$(flock queue.lock -c "head -1 queue.txt 2>/dev/null; sed -i '1d' queue.txt 2>/dev/null" 2>/dev/null)
  [ -z "$task" ] && { sleep 20; continue; }
  id=$(printf '%s' "$task" | sha1sum | cut -c1-8)
  out="findings.d/$W-$id.md"
  tlog="logs/$W-$id.log"
  env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u OPENROUTER_API_KEY -u CLAUDE_CODE_OAUTH_TOKEN \
    opencode run -m "$MODEL" "You are worker $W on a whitehat hunt. Brief: ~/hunt/brief.md. Credentials/local state: ~/hunt/creds.json, ~/hunt/notes.md, ~/hunt/FINDINGS.md, ~/hunt/ids.json if any, and *everyone else's result* in ~/hunt/findings.d/ (grep it first so you do not repeat a dead end).

YOUR TASK FOR THIS SESSION (surgical; finish it; do not wander):
$task

Rules: prove every claim with a copy-pasteable curl + observed status/body; ALWAYS run a control; stay under ~3 req/s; a 401/403/429 is a result, not a failure; write/mutate only inside the resources you own (creds.json marks which), and soft-delete anything you create.

YOUR OUTPUT IS FILES, NEVER stdout. When done:
1. Use your file tools to CREATE $out in this exact format:
## <title>
status: confirmed | hypothesis | dead     class: authz|ssrf|info-leak|session|injection|config|structural|dos|crypto     sev: crit|high|med|low|info
where: <host+path>
what: <2-4 sentences>
proof: <the single curl> -> <observed status and trimmed body>
control: <the falsification test> -> <result>
chain: <what it unlocks, or empty>
2. Then append ONE JSON line per finding to ~/hunt/findings.d/index.jsonl with exactly these keys: {\"id\":\"<STREAM>-<n>\",\"stream\":\"<your stream>\",\"worker\":\"$W\",\"status\":\"...\",\"sev\":\"...\",\"class\":\"...\",\"title\":\"...\",\"where\":\"...\",\"proof\":\"...\",\"control\":\"...\",\"chain\":\"...\",\"detail\":\"$out\"}. one object per line, no prose; that file is the only thing the lead reads.
If it is not in index.jsonl and $out, it never happened." -> "$tlog" 2>&1
  rm -f .done; echo DONE > .done
  sleep 2
done
