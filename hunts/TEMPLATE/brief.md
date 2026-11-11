# Brief — <target>

Rules of engagement injected into every worker prompt. Keep it under ~150
lines; workers read this plus their single task.

## Target

- Primary app: <https://app.example.com>
- API root: <https://api.example.com>
- Docs/OpenAPI: <https://example.com/docs, /api/schema/ if present>

## Identity and credentials

- Account: <role/permission set of the hunt account, e.g. "owner of org X with
  one project; one project-scoped READ-ONLY personal API key">
- Credentials live in: <creds.json in this directory / .env entries — never in
  committed files>
- Resources marked MINE in this hunt: <org id, project id — writes allowed here>

## Politeness

- ≤3 requests/second, one request in flight per worker unless told otherwise
- User-Agent: `whitehat-hunt/1.0 (contact: <email>)`
- Reads preferred; writes only into MINE resources; delete anything you create

## Method

Follow the hunt loop (surface → stack → CVE/code → behavior → serialize →
chain). Prove every claim with a copy-pasteable request and a control. Output
files only: findings.d/<machine>-<task>.md + one JSON line per finding in
findings.d/index.jsonl.

## Already known dead ends

<lead maintains this; workers must grep findings.d/index.jsonl first too>
