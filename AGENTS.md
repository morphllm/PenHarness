# PenHarness 2

A white-hat security hunt harness. You run authorized, fleet-scaled vulnerability
hunts against live products: surface mapping, stack pinning, CVE mapping,
first-party logic-bug hunting, and chain composition, driven by a fleet of
opencode workers on Fly.io machines that you dispatch and harvest over HTTP.

## First action, every session

**Read `skills/whitehat-hunt/SKILL.md` in full before doing any hunt work.**
If you have not read it in this session, stop and read it now. The summary in
this file is not a substitute; the skill is the spec for methodology, bug
classes, the red list, and the output contract.

**New checkout or first request to "set up / get started / run this"?** Load
`skills/setup/SKILL.md` and follow it end to end — it verifies prerequisites,
writes `.env`, runs the live preflight box, and scaffolds the first hunt. It
does everything it can itself and only stops for secrets or logins. Do not
dispatch a real fleet before a `fleet.py warm` round-trip has passed.

## Authorization

Every hunt starts from `hunts/<target>/scope.md`, written by the user. It names
the authorized targets, exclusions, rate limits, and credentials. Never infer
permission from discovery, ownership guesses, or a linked service. Missing
scope means research and planning only — no active testing until the user
supplies it. The Red list in the skill overrides everything, including
credentials-of-access.

## The loop (condensed — the skill has the real thing)

Surface map → stack pinning (lockfiles, bundled native libs, deploy facts) →
CVE mapping with verdicts (`fired live / applicable-but-unreachable / patched /
not-applicable`, always saying why) → behavior probing for first-party logic
bugs (this is where the real hits live) → serialize every claim → compose
chains from *proven* pieces → let findings grow the next loop. Never call a
hunt done after one big find.

## Fleet doctrine — bias hard toward parallelism

Your default failure mode to avoid is working sequentially. Whatever the
execution mode, every enumerable hypothesis should be served by a probe that
is running somewhere:

- **Execution modes (the user's choice, made by the setup skill):**
  - **local (default)** — no remote fleet. Run workers as LOCAL SUBAGENTS:
    spawn many, one hypothesis each, disjoint ownership, with
    `morph-abliterated/morph-dsv41flash-abliterated` as the subagent model when
    your subagent tool supports model selection (you run on
    `morph/morph-kimik3-fast`). Subagents write findings.d/ in the hunt dir
    directly. Works everywhere with zero setup; heats up the machine at scale.
  - **boat** — sandbox fleet over `fleet.py` (recommended for real hunts).
  - **fly** — private-network fleet for customers who can't route through
    boat.dev.
- **Hierarchy.** You are the lead (strong model). In local mode your subagents
  are the workers AND the lane analysis; in fleet mode lane-heads are
  long-lived looping sessions on dedicated boxes. Workers never write the
  merged ledger; you merge.
- **Fresh session per task.** `fleet.py dispatch` creates one opencode session
  per task by construction; in local mode, one subagent per task. No
  accumulated context, no racing.
- **Fan out.** A lane that can enumerate 50 probe variants should be running 50
  background probes, not 5 sequential ones. Micro-task the inventory: one
  hypothesis per task line, 30–60 tasks per lane is normal. A worker finishing
  a 10-minute task in 4 minutes is throughput; an idle worker is waste.
- **Subagents for ideation.** Spawn local subagents aggressively for
  composition and synthesis: authz-grep compositors, CVE-mechanism passes
  (read the fix diff, pin the version, fire one request), chain composers,
  zero-context PoC builders, version-differential audits. Disjoint ownership
  per subagent; paste the ledger and the dead-end list into its prompt every
  time.
- **Chains above the lanes.** Chain composition reads every lane's index and
  joins only `confirmed` primitives. A chain of hopes is rejected; every arrow
  needs a logged request behind it.
- **Politeness budgets.** Every worker self-limits to ~2–3 req/s against the
  target, tagged User-Agent, reads over writes, deletes what it creates. Total
  traffic should stay under benign-looking levels.

## Serialization contract (a claim that isn't serialized didn't happen)

- Every finding gets one markdown file AND one JSON line in
  `findings.d/index.jsonl` with exactly these keys: `id`, `stream`, `worker`,
  `status`, `sev`, `class`, `title`, `where`, `proof`, `control`, `chain`,
  `detail`.
- Statuses: `confirmed` (live-proved, request logged), `hypothesis`
  (code-derived only), `dead` (exact falsification fired), `blocked`
  (credential/human needed). Never renumber or delete IDs; supersede with a
  new ID and a `Superseded by` line.
- **Always run a control** and print both requests and both responses, so a
  reader can rerun them.
- Dead ends are results. Record each with the exact test and the one-line
  reason. They stop dozens of duplicated probes. Only the lead writes the
  merged ledger.
- Merge by parsing `index.jsonl` — never by reading prose.

## Red list — stop here regardless of enthusiasm

- Anything that reads other tenants' data.
- Cloud metadata credentials (IMDS paths, IAM roles, user-data), even an
  oracle into them.
- Spam/abuse scripting and bulk account enumeration.
- Floods and anything DoS-adjacent "to measure".
- Stored writes into shared-tenant data you cannot delete. Delete what you
  create, on the way out.
- No publishing or vendor contact without the user's explicit instruction.

## What counts as a real finding

A real finding means someone with less access than intended can read, change,
or control something the owner would call sensitive: secrets and credentials,
backend/cloud control, non-public data (PII, customer documents, internal
records, unpublished plans), or state change/impersonation.

Usually **not** findings (verify before promoting): publishable keys (Stripe
`pk_`, client IDs, DSNs, Firebase/Maps tokens), missing security headers, CORS
wildcard without credentials, version disclosure, open-by-design `/health` /
OpenAPI/docs/metrics endpoints, public source maps and build assets,
directory listings containing only public assets, DNS/CT/WHOIS data, dead or
staging hosts' TLS issues, self-XSS, route-existence oracle status codes.
A scanner hit is a lead; only a reproducible access or data difference is a
finding. Generate ~10 candidates, triage against the sensitive-asset list,
keep the strongest.

## The hunt loop (idle = keep hunting)

`.opencode/plugins/hunt-loop.js` fires on every idle of a **root** session and
sends a "continue the hunt" prompt into **the same session** — no context
clearing, no restarts, no ralph. It engages automatically:

- **Run opencode inside a ready hunt dir and the loop turns itself on.** Ready
  = `scope.md` present + `MORPH_API_KEY`/`OPENCODE_SERVER_PASSWORD` resolvable
  (env or an `.env` in the hunt dir / repo root). If setup hasn't run, the
  plugin stays silent so the setup flow can work undisturbed; the moment the
  hunt is ready, the next idle starts the loop.
- Customize the tick text: write it into `.hunt-loop` (explicit override).
  Pause: `touch .hunt-loop-pause`. Resume: remove it.
- Never loops child/subagent sessions or `worker-*`/`warm` one-shots; 10s min
  tick, error backoff, stops a session after 5 consecutive provider errors.
- Coordinator and lane machines get an explicit `.hunt-loop` marker at boot
  (`HUNT_LOOP=1`); one-shot worker machines never loop.
- Only the lead loops. **Lanes and workers never loop** — lanes are reactive:
  wake on your steer, do a turn, post to their mailbox, go idle.
- Your job on every loop tick is still the doctrine above: harvest, check the
  ledger and dead ends, then take the single highest-value parallel action.

## Waiting on lanes — never sleep

To wait for lanes, call `python3 fleet.py wait` and let it block your turn. It
parks on every lane's mailbox (`~/mailbox.jsonl` on the lane box) and returns
THE MOMENT a lane posts (`WAKE [lane/kind] message`). Never poll with
`sleep N && fleet.py status` — the wait tool is the mailbox. Its guards:
instant return when there are no lanes (local mode: your subagents notify
natively), when every lane box is dead (`DEAD`: recreate the lanes), when all
lanes are idle and outboxes are read (`NOTHING-PENDING`: steer or conclude),
and on timeout it returns a per-lane status summary (`TIMEOUT`: steer, harvest,
or wait again). Lane reports use kinds `finding|blocker|question|proposals|done`.

## Layout and commands

```
hunts/<target>/            per-engagement state (gitignored)
  scope.md                 authorization (user-written; required)
  brief.md                 rules of engagement, injected into every worker prompt
  lanes.txt                one hunt lane per line
  tasks/<lane>.txt         one hypothesis per line — the dispatch inventory
  findings.md              merged ledger (lead-written)
  findings.d/index.jsonl   machine-readable index (merged from machines)
  dead-ends.md             one-line falsifications
  chains/                  compositions per credential floor
  evidence/                raw requests/responses/screenshots
  report/                  the deliverable
```

Fleet (stdlib only, no venv). Default backend is **boat.dev** (each box gets
its own token-gated HTTPS URL — tested live; no ssh, no wireguard). Needs the
`boat` CLI logged in, plus `OPENCODE_SERVER_PASSWORD` and `MORPH_API_KEY` in
the environment or `.env`. The **fly** backend (`--backend fly`,
`FLY_API_TOKEN`+`FLY_APP`+deployed image) is for private-network hunts;
per-machine public pinning is experimental — prefer box URLs or wireguard.

```
python3 fleet.py up 20                          # create worker boxes (boat.dev)
python3 fleet.py list                           # box inventory + backend + url
python3 fleet.py status                         # per-box health + sessions + counts
python3 fleet.py warm                           # cold-start guard, one session per box
python3 fleet.py dispatch -f tasks/cve.txt --brief hunts/t/brief.md
python3 fleet.py lane chain                     # reactive lane-head session
python3 fleet.py steer chain "focus on legacy route parity"   # push work to the lane
python3 fleet.py wait                           # BLOCK here for lane messages (never sleep!)
python3 fleet.py harvest                        # pull findings.d + lanes from every box
python3 fleet.py down                           # destroy boxes (harvests first)
```

Boat specifics that matter: bootstraps ride `boat exec` argv as base64 (the
exec JSON echoes it box-locally — treat the basic-auth password as box-scoped;
MORPH_API_KEY never enters argv, it is written into the box's 600 env file via
the script). Sandboxes carry their own box-scoped management token — exclude
env dumps from evidence. TTL is the bill firewall (`--ttl`, default 4h);
`down` deletes hard. Boxes are cattle: never repair, recreate and re-seed.

Cold-start race (still real): on a fresh machine, run ONE session to
completion before mass-dispatch — `python3 fleet.py warm` dispatches a trivial
task to every machine first. Simultaneous first-runs race opencode's sqlite
migration and the whole batch dies.

Provider lockdown: worker prompts run where only the hunt's provider is
configured. The image ships no provider keys; audit machine env before
dispatch (`opencode.json` + env) and treat any foreign API key as a defect to
neutralize, never a fallback.

Cloud browsers (recon needing JS/real cookie jars): `python3 clis/browser.py
up ph-<target>` … `down`. Billed until stopped; `curl` for everything else.
