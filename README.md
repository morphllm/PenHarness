# PenHarness

An agentic whitehat security-hunt harness. One lead agent drives a fleet of
ephemeral opencode workers — dispatched, steered, and harvested over the
opencode HTTP API — to find real vulnerabilities in authorized targets:

**surface map → stack pinning → CVE mapping → first-party logic bugs → serialize → chain.**

The methodology and fleet doctrine live in [`skills/whitehat-hunt/SKILL.md`](skills/whitehat-hunt/SKILL.md)
and [`AGENTS.md`](AGENTS.md) (always in context when you run opencode from this repo).

## Architecture

```
┌──────────────────────── operator machine ──────────────────────────┐
│                                                                     │
│   ┌─────────────────┐   tick   ┌─────────────────────────────┐      │
│   │ opencode        │╌╌╌╌╌╌╌╌╌▶│ hunt-loop plugin            │      │
│   │ lead agent      │◀╌╌╌╌╌╌╌╌╌│ idle session ⇒ "continue"   │      │
│   └──────┬──────────┘          └─────────────────────────────┘      │
│          │ fleet.py: up · dispatch · steer · harvest (HTTP only)    │
│   ┌──────┴──────────┐                                               │
│   │ findings.d/     │◀──── harvest: merge each box's index.jsonl    │
│   │ index.jsonl     │                                               │
│   └─────────────────┘                                               │
└──────────┬──────────────────────────────────────────────────────────┘
           ╎ opencode HTTP API · create / steer / stream sessions
           ▼
┌────────────── worker fleet · local, boat.dev, or Fly.io ──────────────┐
│                                                                       │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐      │
│  │ lane-head       │   │ worker          │   │ worker          │  …   │
│  │ long-lived ·    │   │ 1 session/task ·│   │ 1 session/task ·│      │
│  │ strong model    │   │ muscle model    │   │ muscle model    │      │
│  └──┬───────────┬──┘   └──┬───────────┬──┘   └──┬───────────┬──┘      │
└─────┼───────────┼─────────┼───────────┼─────────┼───────────┼─────────┘
      │ infer     │ probe   │ infer     │ probe             …           
      ▼           ▼         ▼           ▼                               
 ┌────────────┐  ┌────────────────────────────────┐                     
 │ Morph API  │  │ authorized target              │                     
 │ std+ablat  │  │ ≤2–3 req/s · tagged UA · clean  │                    
 └────────────┘  └────────────────────────────────┘                    
```

Solid arrows are data; dashed arrows are control. The lead agent plans and runs
`fleet.py`, which owns every worker's lifecycle over the opencode HTTP API.
Lane-heads are long-lived judgment sessions on the strong model; workers are one
fresh session per task on the muscle model. Each worker calls the Morph API for
inference and probes the target directly, writing results to its own
`findings.d`; `harvest` merges them into the single operator-side ledger. The
hunt-loop plugin keeps root sessions moving instead of restarting them.

## Prerequisites

- A target you are **authorized to test** (see `hunts/TEMPLATE/scope.md`).
- The opencode CLI locally (`npm i -g opencode-ai`). Hunts run locally by
  default (parallel subagents), with zero extra setup.
- A Morph API key (standard AND ablated endpoints; one key covers both, wired
  via `{env:MORPH_API_KEY}`).
- Optional: a remote worker fleet when a hunt needs more parallelism than one
  machine holds — [boat.dev](https://boat.dev) (`boat login`) or a Fly.io
  private network (`flyctl`). `fleet.py` drives either over the same HTTP API.
- Optional: [Browser Use](https://browser-use.com) for cloud browsers in
  JS-heavy recon.

`fleet.py` is stdlib-only Python 3.10+: no venv, no pip install.

## Setup (once)

```bash
cp .env.example .env   # fill in MORPH_API_KEY, OPENCODE_SERVER_PASSWORD
opencode               # say "set me up" — it asks local-vs-fleet, verifies keys,
                       # runs the preflight, scaffolds hunts/<target>/
```

## Running a hunt

First time? Open opencode in the repo and say **"set me up"** — the `setup`
skill verifies credentials, writes `.env`, runs a live one-box preflight
(`up` → `warm` → `down`), then scaffolds `hunts/<target>/` from your answers
and seeds the first task inventories from real recon. Or do it by hand:

```bash
cp -r hunts/TEMPLATE hunts/<target>
# fill in hunts/<target>/scope.md (AUTHORIZATION — required), brief.md, lanes.txt
# write one-hypothesis-per-line task inventories in hunts/<target>/tasks/<lane>.txt

cd hunts/<target>
python3 ../../fleet.py up 20            # spin up worker boxes (local, boat.dev, or Fly)
python3 ../../fleet.py warm             # cold-start guard: one session each, serially
python3 ../../fleet.py dispatch -f tasks/surface.txt --brief brief.md
python3 ../../fleet.py lane chains --brief brief.md   # long-lived judgment session
python3 ../../fleet.py status           # health, sessions, finding counts
python3 ../../fleet.py steer chains "prioritize the read-key route matrix"
python3 ../../fleet.py harvest          # merge findings.d from every box
python3 ../../fleet.py down             # harvest, then destroy the fleet
```

Or just open opencode in this repo and say what you want: AGENTS.md tells the
lead agent to read the skill, bias toward parallelism, and use these commands.
`fly/attach.sh "prompt"` attaches to the long-lived coordinator server directly.

## The hunt loop

A plugin (`.opencode/plugins/hunt-loop.js`) keeps root sessions working: every
time the lead or a lane-head goes idle, a "continue the hunt" prompt goes back
into **the same** session — one continuous hunt, never a ralph-style restart.
**Zero-touch:** run `opencode` inside `hunts/<target>/` and the loop engages
automatically as soon as the hunt is ready (`scope.md` + provider keys
resolvable); before that it stays out of the way so setup can run. Custom tick
text: write `.hunt-loop`. Pause: `touch .hunt-loop-pause`. Coordinator and lane
machines loop automatically; one-shot worker sessions never loop.

## Design rules (the short version)

- **Judgment fans out to the strong model; volume fans out to the muscle model.**
  Lane-heads are long-lived sessions; workers are one fresh opencode session
  per task, created by `fleet.py dispatch`.
- A claim that isn't in `findings.d/index.jsonl` didn't happen. Always run a
  control. Dead ends are results. Workers never write the merged ledger.
- Every worker self-limits to ~2–3 req/s, tagged User-Agent, deletes what it
  creates. The **Red list** in the skill (other tenants' data, cloud metadata,
  DoS-adjacent probing, publishing) overrides everything.
- Machines are cattle: recreate and re-seed, never repair. `fleet.py down`
  when done — machines bill until destroyed.

## Layout

```
AGENTS.md / CLAUDE.md     standing agent instructions (always in context)
skills/whitehat-hunt/     the methodology (fleet doctrine, bug classes, red list)
fleet.py                  machine lifecycle + dispatch + steer + harvest (HTTP only)
fly/                      image: Dockerfile, entrypoint, opencode server config, attach.sh
fly.toml                  the long-lived coordinator server
clis/browser.py           cloud-Browser-Use wrapper for JS-heavy recon
hunts/TEMPLATE/           copy per engagement (scope, brief, lanes, tasks)
hunts/<target>/           gitignored working state: findings, evidence, report
```

## Legal

Use this only against targets you are authorized to assess. The harness tags
its traffic, rate-limits, and records everything for a reason; keep it that way.
