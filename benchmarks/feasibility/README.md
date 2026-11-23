# Pentest-benchmark feasibility reports

Read-only feasibility checks written before any compute was spent, 2026-11-22. Kept
because they record *why* each benchmark was chosen or dropped, which is otherwise
easy to lose.

| Report | Verdict |
|---|---|
| `XBOW-HTB-REPORT.md` | XBOW runnable via the krodalabs harness; HTB AI Range has no self-serve path (demo request only). **XBOW was run** — see `../kimi-k31-abliterated/xbow/`. |
| `BOUNTYBENCH-REPORT.md` | Runnable on x86_64 after a provider patch + local amd64 agent image. **Ported and validated** on a boat VM — see `../kimi-k31-abliterated/bountybench/`. |
| `AUTOPENBENCH-REPORT.md` | Runnable in autonomous mode only, needs a pinned commit, a patched paper agent and a throwaway VM. **Deferred** — 1–2 days per model for the weakest signal of the three. |
| `OUTREACH-REPORT.md` | Two leaderboard asks sent (Ridge Security, Darkmoon) offering API access for an independent test. |

Corrections found while actually running them, which these reports did not catch:

- The XBOW harness copy used for that review was a truncated extraction missing
  `models.py`, `util.py`, `adapters/base.py`, `runner.py` and `cli.py`; it could not
  import. The real artifact repo has them.
- The bwrap jail's `--tmpfs /run` hides systemd-resolved's varlink socket, so every
  DNS lookup inside the jail fails on Ubuntu/Pop!_OS. Fixed in `xbow/harness/harness.patch`.
- BountyBench's published agent image is arm64-only, and `_create_and_start_container`
  pulled it on every container start, overwriting a local amd64 build.
