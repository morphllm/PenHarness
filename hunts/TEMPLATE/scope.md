# Scope — <target>

> Written authorization. The lead agent will not perform active testing until
> this file exists and names the targets. Delete the template commentary when
> done.

## Authorization

- Program / agreement: <bug bounty scope URL, signed contract, or internal approval>
- Authorized by: <name, role, date>

## In scope

- <app.example.com>
- <api.example.com>
- <accounts, roles, credentials provided and where they live>

## Out of scope

- <excluded hosts, third parties, payment paths, physical, social>

## Operating constraints

- Rate limit: ≤3 requests/second per worker, no more than ~15 req/s fleet-wide
- User-Agent: `whitehat-hunt/1.0 (contact: <email>)`
- Writes: only into resources this hunt created or explicitly owns; delete
  everything created when done
- Hard stops (the Red list applies regardless of scope): other tenants' data,
  cloud metadata credentials, DoS-adjacent probing, bulk enumeration, any
  publishing or vendor contact without explicit instruction from the user

## Credentials

<never paste secrets in files that get committed — reference .env entries or
a secrets path instead>
