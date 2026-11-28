# Paid dashboard engagements

This package is the authenticated control plane behind the Morph dashboard's **Hack me** flow.
Landing creates an engagement only after its existing privileged-access check and successful Stripe payment. A
separate process runs each engagement through the canonical `fleet.py` lifecycle, so working
directories, fleet state, provider configuration, and OpenCode passwords are never shared between
customers.

```text
Stripe-paid + accepted authorization + privileged-account access
  → Vercel OIDC-authenticated POST /v1/engagements
  → isolated engagement.worker subprocess
  → scope recheck + egress preflight
  → one isolated coordinator VM → warm
  → coordinator spawns native Kimi lanes → each lane spawns DeepSeek child subagents
  → coordinator harvests the nested sessions' findings → fleet down on every exit
  → redacted findings projection for landing
```

## Interfaces

- `POST /v1/engagements`: accepts the strict JSON body in `engagement/models.py`. Repeating the same
  request is idempotent; reusing an id for a different request returns 409.
- `GET /v1/engagements/<uuid>`: returns only `id`, `status`, optional `detail`, and optional `report`.
- `DELETE /v1/engagements/<uuid>`: requests cancellation. The record becomes terminal only after the
  worker confirms fleet teardown.
- `GET /health`: unauthenticated load-balancer health check with no service details.

Every v1 call requires a Vercel OIDC bearer token pinned to the configured team issuer, custom
audience, project name, and `production` subject. No dashboard/shared service token exists.

## Configuration

Non-secret identity and listener configuration:

- `VERCEL_TEAM_SLUG`, `VERCEL_PROJECT_NAME`, `VERCEL_OIDC_ISSUER`
- `PENHARNESS_OIDC_AUDIENCE`, `PENHARNESS_STATE_ROOT`, `PENHARNESS_BIND`, `PENHARNESS_PORT`
- `PENHARNESS_BROKER_BIND` (exact private Fly6PN address; defaults to `FLY_PRIVATE_IP`) and
  `PENHARNESS_BROKER_PORT` (private model-broker listener, default 8081)
- `HACK_ME_MAX_RUNTIME_SECONDS` (default 6 hours)

The runtime also needs the existing Morph provider credential and Fly Machines credentials already
used by this harness. Do not copy them into engagement records, logs, prompts, or reports.

Automatic launch is deliberately inert by default. Enabling it requires all of:

1. A real, verified deny-by-default network boundary around every worker. Proxy environment variables
   alone do not stop a shell from making a direct connection.
2. `HACK_ME_EGRESS_PROXY_URL`, an unauthenticated HTTP(S) proxy reachable only inside the worker
   network. It must allow only the exact paid hostname and reject every non-public destination and
   redirect.
3. `HACK_ME_EGRESS_ENFORCED=1` and `HACK_ME_LAUNCH_ENABLED=1`, set only after the two conditions above
   pass a non-customer-target preflight.
4. The Fly backend. Other backends are rejected until they provide the same isolation and egress proof.

The control plane checks launch configuration before target address resolution. `fly.hack-me.toml` ships with
`HACK_ME_LAUNCH_ENABLED=0`, making a fresh deployment safe for integration tests but not assessments.

## Test

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -v
```

The suite contacts no target and creates no fleet. It covers hostname/TXT/address policy, exact workload
identity configuration, strict payloads, idempotency, report redaction, launch gating, proxy propagation,
forced teardown, and the real local HTTP-to-isolated-subprocess path with launch disabled.


## Live activity

`phase` and six activity counters are persisted with each engagement. The coordinator creates work
dynamically; counts come from its native child sessions and validated output, not a fixed task list. CVE and chain confirmations come only from the harvested JSONL ledger. Public status accepts
these exact fields plus bounded integers; the dashboard cannot invent progress or findings.

A two-label apex includes its conventional `www` host in the coordinator's generated scope and
public-address safe workers; nested hostnames and every other subdomain remain exact-host only.
The duplicate controller-side lookup before provisioning is skipped, while each paid Machine still
rejects non-public targets before OpenCode starts.

Landing polls remote status on demand when due and keeps a five-minute reconciliation fallback.
Explicit supersession records hide only the cited withdrawn findings; raw evidence remains internal
and the public report schema is unchanged. Retention is 7 days for `work/` and worker/cleanup logs,
and 30 days for the public report. `HACK_ME_EVIDENCE_RETENTION_SECONDS` and
`HACK_ME_REPORT_RETENTION_SECONDS` override these defaults. Active and cleanup-pending engagement
records are never pruned; terminal ids and statuses remain for lookup and idempotency.

## Model isolation and prompt-injection containment

Paid workers never receive `MORPH_API_KEY`. Their OpenAI-compatible base URL is
`/v1/engagements/<id>` on the control plane. OpenCode authenticates with the existing per-engagement
credential, and `engagement/broker.py` forwards the request to the existing Abliterated endpoint using
the provider key only in the control-plane process.

The broker allows exactly `morph-kimik3` (coordinator and lane heads) and
`morph-dsv41flash-abliterated` (workers), routes both to the Morph API with this run's key, and
charges both to one durable $1,000 ledger at Landing's list prices. It allows only chat completions,
and caps `max_tokens` at 32768. There is no request-count cap: dollars are the only per-engagement
limit. It never logs prompts, responses, credentials, or machine configuration. No harness machine
receives Fly management credentials either; those stay on the control plane.

`HACK_ME_LOCAL_SUBAGENTS=1` makes paid execution native OpenCode nesting on one coordinator VM.
The OpenCode configs cap `subagent_depth` at 2 and pin `kimi-lane` to Kimi K3 with permission to launch
only `deepseek-worker`; that leaf is pinned to DeepSeek Abliterated and has `task: deny`. For every
distinct investigation or chain idea, the root spawns a native Kimi K3 lane child; that lane spawns
its own DeepSeek children in parallel. No lane or worker machine exists and the paid path does
not use the fleet-control bridge or a remote worker fleet. Only the root coordinator loops. The root's
exact private address remains the only authorized broker/control source, and the provider key and Fly
management credential never enter that machine.

Model traffic is served only by a separate listener bound to the exact private Fly6PN address on
port 8081. The public management listener on port 8080 has no `/chat/completions` route, and the
private broker has no management or OIDC routes. Startup fails rather than binding the broker to
`::`, a public address, or another fallback interface.

This is not an invitation to send a normal full-scope user key to workers. The current key system has
no TTL, model allowlist, or per-key spend cap. A future issuer can add a short-lived, user-attributed,
Abliterated-only credential in the control plane if attribution is needed. Until then, request and
output limits bound broker use, while the mandatory worker-egress boundary limits what an injected
agent can reach. Target pages, snippets, errors, and tool output are untrusted data, never instructions.


## Broker TTL and worker-IP binding

Each broker credential has a SHA-256 token hash, a hard `expiresAtUnix`, and an exact source-IP
allowlist. The token itself is not stored in that record. The allowlist starts empty and is populated
only after the paid workers start, using their direct private Fly6PN addresses. An empty allowlist
denies every caller, including callers presenting the right token.

Both `HACK_ME_MAX_RUNTIME_SECONDS` and `HACK_ME_BROKER_TTL_SECONDS` default to **21600 seconds (6
hours)**. Neither can exceed 6 hours, and broker TTL cannot exceed the engagement runtime. Every
broker call revalidates all three properties: unexpired engagement, direct peer IP in the allowlist,
and matching token hash. Public addresses, relays, and `X-Forwarded-For` are not accepted as proof of
worker identity. Model traffic uses only the control plane's private `.internal:8081` endpoint;
private traffic bypasses the target egress proxy.

The supervisor also enforces the persisted runtime deadline. It first requests cancellation and gives
the worker `HACK_ME_TEARDOWN_GRACE_SECONDS` (default 60) to harvest and destroy its fleet. A stuck
subprocess is then terminated and, if necessary, killed. Every active child exit triggers reconciliation
into cleanup only, never another assessment launch. Live PID adoption verifies command and start-time
identity; failed identity inspection retries without racing a possibly live worker.

A cleanup failure persists `cleanupPending` and schedules cleanup-only retries. A partial machine teardown
preserves only the unresolved machines, and an already-absent machine counts as deleted. The engagement
becomes terminal only after required destruction and credential revocation succeed; cancellation alone
never claims teardown completed. Completed, failed, and cancelled engagements cannot call the broker.


## Secret-free workers (2026-11-23)

Controller and paid workers **must run in separate Fly apps**. Fly can inject app-scoped
Secrets independently of `machine_env`, so omitting a provider or management key there does not
prove worker isolation. `FLY_APP` selects the secret-free worker app; `HACK_ME_CONTROL_FLY_APP`
selects only the controller app's image. App-wide Secrets belong only on that controller app, never
on the worker app. The private broker still works across apps over the same 6PN.

Paid worker Machines no longer have an anycast HTTPS service. The controller reaches their OpenCode
API only over private Fly6PN. This removes the public-open-code-plus-password attack surface from
the threat model rather than merely expiring its password.

Each engagement mints its own broker secret (`secrets.token_urlsafe(32)`), distinct from the
OpenCode server password. The controller stores only its SHA-256 (`.broker-credential.json`,
bound to the workers' private addresses and the engagement's TTL) and hands the plaintext to the
workers as `HACK_ME_BROKER_TOKEN`; it is never written to disk and is revoked with the run. The
public `engagement-worker` marker is still rejected by the broker, even from an allowed IP, so a
worker that ever falls back to it fails closed. Paid launch stays disabled until the upstream
credential below is resolved.

`box_req` honors `privateOnly` even when Fly returns an `instance_id`, preventing an accidental
fallback to the public anycast URL. A regression test checks both the absence of public services and
that request routing uses the direct private address.


The direct-peer binding is ours; an upstream API allowlist is not. Morph's current general API key
schema does not provide machine-bound credentials, so there is no claim that a separately stolen
control-plane provider key would be rejected on a network outside Fly. Production launch still needs
one of: a gateway-enforced source allowlist for the assessment credential, or a scoped upstream key
issuer that enforces it. The worker-side phantom token is never such a credential.
