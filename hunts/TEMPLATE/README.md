# Hunt template

Copy this directory to `hunts/<target>/` and fill it in before any testing:

```bash
cp -r hunts/TEMPLATE hunts/<target>
```

1. `scope.md` — **required**. Written authorization: targets, exclusions, rate
   limits, credentials. The lead agent refuses active testing without it.
2. `brief.md` — rules of engagement injected into every worker prompt
   (credentials, owned resources, tagged UA, politeness budget).
3. `lanes.txt` — one hunt lane per line (a CVE class, a logic-bug class, a
   recon direction). Invent lanes freely as understanding shifts.
4. `tasks/<lane>.txt` — one hypothesis per line; this is the dispatch
   inventory for `fleet.py dispatch`. 30–60 tasks per lane is normal.

Everything else (`findings.d/`, `dead-ends.md`, `chains/`, `evidence/`,
`report/`, `remote/`) is produced by the fleet and the lead. `fleet-state.json`
appears here when you run fleet commands from this directory.
