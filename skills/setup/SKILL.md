---
name: setup
description: Take a fresh PenHarness checkout to a working hunt in one guided pass — ask the user the execution-mode question (sandbox fleet vs local), verify prerequisites, write .env, run a preflight, then scaffold hunts/<target>/ and seed the first task inventories. Use when the user asks to set up, get started, run their first hunt, or when the environment has never been verified. Does everything it can itself; stops only for the mode choice, secrets, or logins the human must provide.
---

# Setup — zero to first hunt

Run this top to bottom. Idempotent: re-running after a failure continues where it stopped.

## 1. Repo sanity

- Confirm you are in the PenHarness root (`AGENTS.md`, `fleet.py`, `fly/`, `skills/` present).
- `python3 fleet.py --help` works. If python3 < 3.10, stop and say so.

## 2. Execution mode — ASK THE USER (do not guess; this is the one required question up front)

Every agent tool (opencode, Claude Code, …) can already run parallel subagents locally —
that works with zero extra setup and is the default. Ask:

> **Run hunts on a sandbox fleet or locally?**
> The sandbox fleet (boat.dev) is **recommended**: a serious hunt spins up a lot of parallel
> agents, and running them on this machine will heat it up and eat its resources. If you have a
> boat.dev account, run `boat login` now — one step, your browser opens, done. If not, no
> problem: everything runs locally as parallel subagents on this machine, and you can switch
> later by running `boat login` and re-running setup.

Record the answer: append/update `FLEET_BACKEND=local` or `FLEET_BACKEND=boat` in `.env`.

- **boat** → verify `boat status --json` (logged in) and `boat limits` (nonzero capacity).
  If auth is missing, hand the user the exact command and WAIT; never attempt login yourself.
- **local** → skip every sandbox check. Dispatch and workers run as local subagents;
  same ledger discipline, no boxes, no external dependency. (If the user ever asks about fly:
  private-network fleet for customers — FLY_API_TOKEN + FLY_APP + `fly deploy`; README covers
  it. Never offer it proactively.)

## 3. Prerequisites (check each; fix what you can, hand off what needs a human)

| Check | Command | If missing |
|---|---|---|
| opencode CLI (lead) | `command -v opencode` | `npm i -g opencode-ai` if npm exists, else tell user. Lead-friendly, not blocking. |
| Local secrets | `.env` with `OPENCODE_SERVER_PASSWORD` and `MORPH_API_KEY` set and non-empty | Create `.env` with mode 600 (start from `.env.example`). Generate the server password (`openssl rand -hex 24`). Ask the user for MORPH_API_KEY only if absent — never echo, never commit. One key covers BOTH providers (workers use the ablated endpoint, lanes the standard one). |
| Key validity (cheap, no boxes) | curl one `chat/completions` probe with `max_tokens: 8` against BOTH `https://api.morphllm.com/v1` (model `morph-kimik3-fast`, lane default) and `https://test-rdu-bb4-abliterated.morphllm.com/v1` (model `morph-dsv41flash-abliterated`, worker default), expecting HTTP 200 on each | The key is wrong for that endpoint. Tell the user which endpoint 401'd; ask for the right key. Do not print the key. |

## 4. Live preflight (mandatory; this is the "it actually works" test)

**boat mode** — from the repo root, with `.env` loaded:

```sh
mkdir -p hunts/preflight && cd hunts/preflight
python3 ../../fleet.py up 1 --ttl 1200 --prefix preflight
python3 ../../fleet.py warm                                   # validates worker default route
python3 ../../fleet.py warm --model morph/morph-kimik3-fast   # validates lane/coordinator route
python3 ../../fleet.py status        # health:ok, sessions non-busy
python3 ../../fleet.py down --force  # destroy proof
```

Both warm round-trips MUST pass on the exact default models — do not substitute a cheaper model.

**local mode** — the curl probes above ARE the preflight. Then run one real local subagent
(the agent coordinates it as usual): a 1-minute task ("read AGENTS.md, report the fleet modes
in 3 lines") to prove subagent fan-out works on this machine. That's all.

Interpretation (boat mode; all observed live in testing):

- Box fails health within 240s → serve never came up; re-run once, then report the box's
  `logs/serve.log` tail (via `fleet.py status` or `boat exec`).
- `warm` returns fast but the session errored at the provider → key invalid for that endpoint,
  or the model id isn't in the box's `fly/opencode.json` / the user's catalog. Errored sessions
  vanish from `/session/status` in ~1s with no assistant message; read the box's session
  messages for the real error before retrying.

## 5. Scaffold the first hunt

Ask (one message, four questions): target product + URLs, authorization basis
(program/contract/ownership; name and date), available credentials and their roles, and any
explicit exclusions.

Then:

1. `cp -r hunts/TEMPLATE hunts/<slug>` (short lowercase hyphenated slug from the target).
2. Write `scope.md` from the answers (keep the Red list verbatim; never soften it). If the user
   cannot state an authorization basis, STOP here: offer passive research only and say so.
3. Write `brief.md`: target URLs, account/credential description (credentials by env name, never
   values), owned-resource markers, politeness budget.
4. Write `lanes.txt`: 6–10 lanes tailored to the target (surface, stack-cves, scope-abuse,
   route-parity, outbound-fetch, binary-decode, credential-transport, paid-feature-gates,
   plus target-specific ones; always end with chains).
5. Seed `tasks/surface.txt` from real recon you do NOW (redirect chains, `curl -D-` on the main
   hosts, crt.sh subdomains, docs product menu) — 15–30 one-hypothesis lines, plus 30–60 lines
   in the highest-value target-specific lane. Task lines must be worker-sizable probes, not
   questions.

## 6. Handoff

Print, short:

```text
setup: mode=<local|boat> | keys ok (both endpoints 200) | preflight ok
hunt: hunts/<slug> ready (scope, brief, N lanes, M tasks seeded)

Run it:
  cd hunts/<slug> && opencode    # the hunt loop engages BY ITSELF when ready
or manually (boat mode):
  python3 ../../fleet.py up 20 && python3 ../../fleet.py warm
  python3 ../../fleet.py dispatch -f tasks/surface.txt --brief brief.md
```

Do NOT launch the full fleet / worker swarm as part of setup. The first dispatch belongs to the
user (or the agent they explicitly start), not to setup.
