# whitehat-hunt fleet harness

The seven scripts that run the whole hunt. They sit next to `SKILL.md` — copy them alongside a target's
`common.md` (the rules-of-engagement preamble) and one `streams/<stream>.md` per attack lane.

## Placement

```
your-hunt/
  common.md          (shared rules: method, politeness, credential rules, the ledger contract)
  creds.json         (any credentials you've earned; the workers read them when present)
  queue-<stream>.txt (one shard per line, task-sentences)
  streams/<stream>.md
```

Then `<skill_dir>/ops/<script>.sh`:

| script | what it does |
|---|---|
| `setup.sh` | one-time per sandbox: installs tmux2 + mailbox/agents plugins, lays down ssh + hunt tree, writes an opencode config (change the provider to yours at line 85). Passed `boat new --setup-file`. |
| `worker.sh` | one-task-per-fresh-session worker loop on a box: pops a `flock`ed queue, runs opencode once per task, writes findings as `~/hunt/findings.d/<w>-<id>.md` + `index.jsonl`. |
| `mailbox.sh` | runs on each box: PREPENDS lead messages (via tmux2 mailbox or a dropped `~/hunt/lead-msg.md`) to the work queue so free workers pick them up instantly. |
| `launch.sh` | creates one boat sandbox, attaches an ssh entry, links `tmux2 remote-attach`, grants mailbox access (`tmux2 plugin-peers allow`), pushes brief + creds + queue, starts 3 workers + the reader. |
| `enqueue.sh` | append tasks to one box's queue atomically: `./enqueue.sh cve -f tasks/cve.md`. |
| `tell.sh` | deliver a lead message to one or all boxes: `./tell.sh all "STATE: ..."`. |
| `inboxd.sh` | lead-side drain: `sh inboxd.sh /tmp/hunt-inbox.log &` and every report lands in the log. |

## Current-usage example (how posthog.com ran it)

- 19 stream boxes, 3 workers per box, then one extra 10-box adjunct much higher-density lane on a flash model.
- The lead polls `inboxd.sh` (one process), answers go via `tell.sh`, tasks via `enqueue.sh`.
- One show-stop discovery: opencode's headless auto-REJECT on permission prompts. `setup.sh` writes `permission=allow` for exactly this reason.
- One design rule: fresh session per task — never a long session that accumulates context across
  dozens of turns.

## Typical fleet loop

1. Build `common.md` + `queue-<stream>.txt` + `streams/<stream>.md`.
2. `for s in <streams>; do ./launch.sh $s; done`
3. `sh inboxd.sh &`
4. `for s in <streams>; do ./enqueue.sh $s -f rounds/r1-$s.txt; done`
5. `sh tell.sh all "CREDS LIVE: creds.json — go."`

And that's everything these scripts assumed about the environment: `boat`, `tmux2` (version `wasm-plugins-…`), `.local/share/tmux2` on the lead, sqlite3 on the lead, and credential bootstrap left to the hunt itself.
