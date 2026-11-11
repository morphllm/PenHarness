---
name: whitehat-hunt
description: Run a serious multi-agent whitehat security assessment against a live product (SaaS, APIs, platforms) — the loop that produced real high-severity findings on a mature open-source analytics SaaS (unauthenticated SSRF with a content pipe, read-scope→write classes, binary decode amplification, cross-tenant telemetry). Use this when the user wants surface → stack → CVEs/code → logical inconsistencies → chains, run as a coordinated fleet of opencode workers on Fly machines, consolidated into an evidence-graded ledger and report.
---

# Whitehat hunt — the reproducible loop

This skill encodes a proven methodology for real vulnerability discovery against mature live
services, authored from a full-scale hunt against a mature open-source analytics SaaS whose
defensible ledger included: an **unauthenticated SSRF with verified content flow** (stack-trace
symbolification fetched attacker-chosen URLs and resolved them into API-visible events),
**read-scope→write classes** (query-cancel, logs-export, LLM-assistant run, and read-key control
of a project's integration installations), **a paid feature fully usable on a free org**,
**126-byte → ~1.9 GB memory amplification live-fired against production with receipts**, and
**469-byte → ~12 MB unauthenticated parser amplification**, plus 40+ documented dead ends.

**Do not use this skill for targets without authorization.** Whitehat only, law-safety-first:
tagged UA, ≤3 req/s per worker box, reads on everything public, writes only into resources you
own or created, delete what you created when done, and STOP at the escalation edges in the
**Red list**.

## The loop (the whole methodology in one paragraph)

**Discover the surface → pin the stack → mine CVEs and read code for first-party logic bugs →
probe behavior with the cheapest credible credentials → serialize every claim into a
machine-readable ledger → compose chains from that ledger → let those new findings grow the next
loop.** Run it continuously; never call it done after one big find.

```
┌──────────────┐   ┌──────────────────┐   ┌────────────────────┐
│ 1. SURFACE   │──▶│ 2. STACK (pinned)│──▶│ 3. CVE+CODE read   │
└──────────────┘   └──────────────────┘   └────────────────────┘
        ▲                                            │
        │        ┌──────────────────┐   ┌────────────▼──┐
        └────────│ 6. CHAIN compose │◀──│ 4. BEHAVIOR   │
                 └──────────────────┘   └────────────┬──┘
                         ▲                           │
                 ┌───────┴────────────┐      ┌───────▼───┐
                 │ 5. SERIALIZE       │◀─────│ proofs    │
                 │  (index.jsonl+md)  │      └───────────┘
                 └────────────────────┘
```

## Phase 0 — posture before anything

- This is a live service, not a lab. Publish everything under a tagged User-Agent (e.g.
  `hunt-lead/1.0 (whitehat; contact@example.com)`), cap every worker at ~2–3 req/s, prefer reads
  over writes, never perform an action whose impact you cannot reverse.
- Establish credentials cheaply and honestly: the *minimum* you want is one legitimate account in
  the product (your own org with one project is ideal), plus one project-scoped read-only
  personal API key. If signup guards (Turnstile/WAF/verification) block that, use a user-supplied
  key. For public surfaces, the service's *public* tokens shipped to customer sites are your
  zero-cred floor.
- What every hunt needs ready up front: the fleet CLI (`fleet.py`) and its credentials — the
  `boat` CLI logged in (default backend) plus `MORPH_API_KEY` and
  `OPENCODE_SERVER_PASSWORD` — one source-of-truth findings dir (`hunts/<target>/`), and one
  account with a deliberately crafted set of permissions.

## Phase 1 — Surface map (what exists)

Do, in order: (1) every product the vendor markets (the docs product menu); (2) hosts: redirect
chains + `curl -D-` on every candidate domain, then crt.sh/developer zones/CAT/certspotter for
subdomains and historical DNS; (3) every JS bundle, and every header a page leaks (CSP
especially). Now classify every host by `Server:` header, LB/CDN, and deploys (server-timing).

Highest-yield deployment facts to name explicitly per host, because they become chain facts:

- **"Marketing estate" vs "application estate" are often different stacks** — different CVE maps
  entirely.
- **A CSP string is a door map** — hosts inside `connect-src`/`img-src`/`script-src`/
  `report-uri`/`frame-src` are already more trusted than anything else; enumerate and probe every
  one (the reference hunt found an internal edge worker, an agent proxy, an AI gateway, webhook
  receivers, and a live event stream this way).
- **`server-timing` reveals the shape**: one line (`server-timing: django;dur, pg;dur, ch;dur`)
  proved one app per region backed by one Postgres + one column store. That means *one* authz
  miss is every product's miss.
- **Check for a published OpenAPI schema** (`/api/schema/`): the reference target served ~1,800
  paths unauthenticated. Whatever the product, an OpenAPI dump is now the route map of the whole
  hunt.

## Phase 2 — Stack pinning (what is *actually* deployed)

For open-source targets: **the source is the spec — read it**. Clone sparse
(`git clone --depth=1 --filter=blob:none --sparse`); keep a pruned tree (backend + product
backend + frontend src + lockfiles, no tests/fixtures/media — 112 MB instead of 827 MB in the
reference case).

Pin everything with proof, in order:

1. Lockfiles (`uv.lock`, `package-lock`, `Cargo.lock`) at the deployed commit — never a version
   by guessing.
2. **Native libs bundled inside package wheels** — the single best ROI trick. Pull a Pillow
   wheel: the bundle contains its own zlib-ng, libjpeg-turbo, libtiff, freetype, webp, openjpeg —
   each with its own advisory history.
3. Dockerfiles/helm/env-vars, per-component in the repo (self-hosted + cloud values).
4. Browser-native stacks (playwright/puppeteer clusters, headless-browser services) and
   orchestration (Temporal/celery).

Write each deployment fact as a numbered line. Chain composers fuse those into chains ("one app
per region + single team boundary" is how surface findings combine into cross-tenant stories).

## Phase 3 — CVE mapping (pinned version → advisory → reachable or not)

For each pinned component: OSV (`api.osv.dev`), NVD, GHSA, the vendor's security blog, and every
release-since-your-pin in the upstream changelog. **Verdicts are `fired live /
applicable-but-unreachable / patched / not-applicable` — and *saying why* is mandatory.** A CVE
in a dependency you cannot reach from the network is not a finding.

What paid off hardest:

- **Native libraries bundled inside wheels** — codec/decompression chain hits where your request
  can reach them.
- **Versions with per-region drift** (CDN/edge deploys rolled separately from origin).
- **The mechanism in the fix diff**: a codec confusion (a web framework's `JSONParser` letting a
  user-chosen Python charset drive decode of the request body: `charset=bz2_codec`) was validated
  with one request and avoided multi-hour fuzzing.
- Keep a HARD-DEAD ledger (~70 entries in the reference hunt) for everything you *thought* might
  exist and probed dead; the fleet never re-burns time on the same leads.

## Phase 4 — Behavior probing: first-party logic bugs (where the real hits live)

The greatest hits of the reference hunt were **not dependency CVEs** — they were first-party
logic flaws where a documented guarantee and the real gate diverge. Hunt by class with the
cheapest honorable credential first. Classes that scored:

1. **Scope-label ↔ side-effect mismatch.** When the permission model is per-route correct but
   the scope **names** lie — a `logs:read` credential that creates an export asset and enqueues
   compute; a `query:read` one that cancels *any* running query in the project without ownership
   check; an `insight:read` one running the whole LLM assistant and persisting a conversation;
   a read key creating/patching/deleting/sharing the project's third-party installations; every
   mutation of your own product record via an org-level PATCH through a project-scoped key. The
   family rule: **a POST verb at a `:read`-adorned route always earns a probe.**
2. **Anything that bounds a decision to a client-controlled value**: UA-based server-side flag
   filtering (a `/decide`-style endpoint returning server-only flag definitions and payloads);
   privileged-skip branches honored only on internal scope objects but reachable by
   project-scoped keys; empty-scope-means-unrestricted handling.
3. **Public-token write surfaces** (event capture endpoints, identity auto-provisioning, `$set`-style
   person-property overwrites of "initial" fields).
4. **Hidden and parallel URL trees** (`/api/environments/*`-style v2 trees, legacy v1 routes at
   root) — test parity per route (authz? serializers? verbs?); one legacy alias tree produced
   another full share of "this shouldn't answer my scoped key" doors.
5. **Binary decode paths**: image libraries (`ImageOps.mirror`-style operations force full decode
   of an uploaded file), sourcemap symbolification (fetches frame URLs from ingested stack
   traces), file uploads with decode-before-check zips (compress-before-parse).
6. **Outbound-fetch mechanisms**: webhook destinations, subscription/alert deliveries, LLM tool
   fetches, rasterizers (headless Chrome), and every export/import path. Measure the egress — IP,
   UA, headers, redirect-following, DNS resolution. In the reference hunt the *only* transport
   capable of reaching internal paths was a forward proxy in front; the rest are vetoes you have
   to map.
7. **Credential transport details**: is the personal API key accepted as a *URL parameter*? Does
   any paginated `next` URL echo it back? Does `OPTIONS` allow write verbs cross-origin with a
   `*` origin? Do cookies or SDKs place it in URLs by default? (Transport of a powerful credential
   over an inherently leaky channel — CORS verdicts decide blast radius.)
8. **Client-side-vs-server enforcement of paid features**: features whose *gate is only in the
   frontend* — the reference hunt enabled a paid product server-side with an unauthenticated
   ingest request.

## Phase 5 — Serialize (the one shape everything shares)

The discipline that keeps a fleet-scale hunt *reproducible per-claim*:

- **Every finding has two serializations**: a short markdown block AND **one JSON line** in
  `findings.d/index.jsonl`, same fields (`id/stream/worker/status/sev/class/title/where/proof/
  control/chain/detail`). If it's not in JSONL it doesn't exist — merge by parsing JSONL, never by
  reading prose.
- **Every falsification is a result, dead ends included.** Document each dead end with the *exact
  test* and the reason (e.g. "needs a write-scoped credential"). They prevent dozens of
  duplicated probes and stop silent reopening of closed leads.
- **Always run a control.** Print both requests and both responses so a reader can rerun them.
  When new evidence disagrees with the prior verdict (a cookie-domain ATO story died on a
  host-only session cookie), **rewrite the ledger** — never leave the inflated claim visible.
- **Status discipline:** `confirmed` (live-proved with the request), `hypothesis` (code-derived
  only), `dead` (exact falsification fired), `blocked` (credential/human needed). **Never
  renumber or delete IDs**; supersede with the new ID and a `Superseded by` line.
- **Proofs are first-class artifacts**, not footnotes: one request + one answer + one control,
  all verbatim.

## Phase 6 — Chain composition (how chains actually form)

A chain is a graph join, not a stack. Draw the capability graph before naming the chain: boxes
are primitives you have fired, arrows are labelled with the assumption that lets each arrow
exist, and the end state is boxed. Any arrow you cannot point at a logged request for is a hope,
not a link.

Once a batch of findings exists, compose chains against the shared deployment map. The six
compositor slots:

- `zero-to-high`: from zero credentials.
- `read-key-empire`: from a minimal read-scoped personal API key, treat each rung.
- `cross-tenant`: every candidate judged on whether *data actually crosses* (only one shape fired
  in the reference hunt — that's honesty).
- `doS-max`: maximum vendor-side spend per attacker-byte with ceilings (throttles, budgets,
  worker planes).
- `identity-takeover`: imperfect-ATO compositions with the single missing primitive per chain.
- `infra/machine-boundary`: anything that touches internals; static-secret receivers,
  worker-heartbeat disclosures, the proxy boundary shape.

Rules: each composer gets the full board (`findings.d` + the ledger + the chain table) and must
mark every link with IDs from already-proven pieces; fantasy compositions are rejected outright.
Composer output becomes queue work for the fleet and is NOT added to the ledger until
independently verified.

## Fleet orchestration (fleet.py + opencode serve over HTTP)

- **Topology law: judgment fans out to the strong model, volume fans out to the muscle model, and
  everything below the lead is background by default.** A *lane* is any hunt idea worth pursuing
  (a CVE class, a logical-inconsistency angle, an invariant to falsify, a recon direction —
  invent lanes freely, never a fixed numbered list). Every lane gets: one lane-head session
  (strong model; reads results, decides next probes, seeds work) plus worker swarms doing the
  parallelized volume (muscle model, one *fresh opencode session per task* — the dispatch CLI
  creates a session per task by construction).
- **Worker boxes: boat.dev sandboxes** (default backend). `python3 fleet.py up N` creates N boxes,
  bootstraps each with the harness (AGENTS.md, skills, plugin, provider config pinned,
  `permission: allow`), exposes opencode's port on a per-box token-gated HTTPS URL, and health
  checks it — all over HTTP, no ssh, no wireguard, no image builds. TTL is the billing firewall.
- **Lane-heads are reactive sessions on dedicated boxes** (`python3 fleet.py lane <name>`) —
  only the lead loops; lanes never do. The lead steers them with `python3 fleet.py steer
  <name> "..."`. When a lane has something the lead must see, it appends one JSON line to its
  box's `~/mailbox.jsonl` (kinds: finding|blocker|question|proposals|done). The lead waits on
  those mailboxes with `python3 fleet.py wait` — a blocking call that returns the moment any
  lane posts, guarded against dead fleets, nothing-pending, and timeout-with-summary. The lead
  NEVER waits by `sleep`; sleep-polling is a defect. Lane-heads also propose tasks as JSONL
  (`lanes/<lane>-proposals.jsonl`); the lead harvests, dedupes, and dispatches.
  Workers never write the ledger and nobody below the lead posts externally. Chains are composed
  above the lanes (a standing chains lane reading every lane's index), never inside them.
- **Dispatch discipline:** one HTTP fan-out per batch, all machines in parallel (fleet.py does
  this; do not devise your own round trip). Micro-task the inventory — 30–60 one-hypothesis
  tasks per lane is normal. A lane with 50 enumerable probe variants should have 50 background
  sessions running.
- **Cold-start the fleet before mass-dispatch.** N simultaneous first-run opencode processes on a
  fresh machine race the sqlite migration and all die; `python3 fleet.py warm` runs one trivial
  session per machine to completion first. Corollary: your first seeding batch is expendable —
  budget a re-seed.
- **Pin model aliases in the server config** (`fly/opencode.json`) and verify per machine before
  seeding: `python3 fleet.py status` shows sessions flowing; a bad alias fails with a generic
  server error that looks transient and eats hours.
- **Provider lockdown.** Machines must expose only the hunt's provider. The image ships no API
  keys; audit any machine env you did not set and neutralize stray keys — one opportunistic
  provider fallback and fleet prompts flow to a third-party API.
- **Rate & politeness:** each worker self-limits ~2–3 req/s against the target; the fleet holds
  ~10–15 rps total — noise-under-benign on a SaaS.
- **Harvest is the only merge path.** `python3 fleet.py harvest` pulls `findings.d/index.jsonl`
  (and per-finding docs + lane proposals) from every machine and merges locally, deduped by
  finding id. Only the lead writes the merged ledger and the report.
- **Teardown with the discipline of a bill:** `python3 fleet.py down` harvests, then destroys
  every machine. Machines are cattle — if one dies, recreate and re-seed from the lead's task
  files; never attempt repair.

## The subagent drives that won

Winners from the reference hunt, ranked by return-on-effort:

1. **Source-grep compositors** (authz-grep, enterprise-gate): semantic mismatch mining in source
   — produced the entire "read-scope→write" class before any fleet would have fuzzed it.
2. **Chain composers** (read-key-empire, cross-tenant, infra): fused *proven ledger facts*
   into new chains.
3. **CVE mechanism pass** (read fix diffs, pin versions, fire a single live request).
4. **Zero-context PoC builders** (crafted-by-hand wire bytes + measured payload amplification on
   the exact deployed version → two-line live proof).
5. **Version-differential audits** (upstream-fixed-vs-deployed first, then code review per
   request profile).
6. **Per-product verticals** (symbolification SSRF, forum, browser render stack): file-by-file
   component work that landed the day's actual severity leaders.

Subagent lessons that cost money to learn:

- **Never hand a subagent a fragile prompt without context** — give the ledger + the dead-end
  litany; reuse otherwise re-digs the same hallway at cost.
- **Disjoint ownership always**: one topic per subagent; nothing overlaps.
- **The cleanest output is JSONL**; prose is what you extract the JSONL from.
- **Escalation respect is a requirement**: the subagent that hits a legal/ethical boundary should
  *stop the probe*, narrate carefully, and write the minimal reproduction cleanly.

## Red list (stop here, regardless of enthusiasm or credentials-of-access)

Escalate none of these without written vendor authorization:

- **Anything that reads data belonging to *other tenants*.**
- **Cloud metadata credentials** (IMDS paths, IAM role names, user-data). Even a status/oracle
  into these is over the line.
- **Spam / abuse scripting** (mass-registering identities, bulk account existence scanning,
  emails at scale).
- **Floods designed to measure**: anything that can double as DoS by accident — always one-shot
  evidence, always gentle curl at ≤3 req/s.
- **Stored-channel writes into shared-tenant data** — if you can't delete the probe object you
  made, don't make it.
- **No publishing** — nothing public until the vendor's security team sees it first.

## The output contract

A hunt is done when the ledger keeps the following under one root (`hunts/<target>/`):

1. `findings.md` — numbered entries, each with status/class/sev/where/what/proof/control/chain,
   the EXACT request, the observed response, and every claim verified with a control.
2. `findings.d/index.jsonl` — deduped JSON for crunching.
3. `dead-ends.md` — one-line reasons each, verbatim.
4. `chains/` — BEFORE/AFTER compositions per credential floor, each with a single decisive
   falsification named.
5. **A minimal repro bundle** — one or two requests per headline finding, ready to paste into a
   disclosure email. Deliver findings to the *user*, never to the vendor, unless the user
   explicitly asks.
6. **The report** — paragraph-per-class, one page per entry, plus a shared deployment-facts
   appendix that proves why the classes connect.

## The biggest levers — what makes it sing

1. **Reading their open-source repo first.** It is the spec; the authz and serialization rules
   all live there.
2. **A genuinely read-scoped credential + a route matrix.** The "scope names lie" class is the
   most-consequence-creating family and isn't reachable any other way.
3. **Hidden URL trees + OpenAPI parity sweeps** — unexpected authz gaps live on unlisted routes.
4. **Credential transport chains** — key-in-URL docs, preflights passing write verbs with
   wildcard CORS, pagination URLs echoing credentials.
5. **Error-tracking symbolification** — every SaaS symbolicator is a fetcher; ask "what does
   frame.filename do?" and measure the egress.
6. **Binary decode-before-check** — crafted bytes cost nothing and spend the server.
7. **Fresh-session-per-task workers over HTTP** — hundreds of parallel autonomous workers that
   never mix contexts, never race, and never stall.
8. **Composition of already-proven pieces** — chains of confirmed facts, never chains of hopes.
9. **Honest death-lists** — dead-end contracts are the tax you pay so no one ever pays them
   again.

Use this only against targets you're lawfully permitted to assess. Good hunting.
