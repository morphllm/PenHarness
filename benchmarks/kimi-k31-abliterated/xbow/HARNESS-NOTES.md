# Running XBOW validation benchmarks against Morph models

Notes for the `morph-kimik3` / `morph-kimik3-abliterated` runs. Everything below is
already applied in this checkout (`~/xbow/harness`); this file records what was
changed and why, so the run is reproducible and honest about its deviations.

## Provenance

- Harness: `krodalabs/coding-agent-research-artifact` (Apache-2.0), commit `4dc9df7`,
  the artifact for "Baselines Before Architecture: Evaluating Coding Agents for
  Autonomous Penetration Testing".
- Benchmarks: `xbow-engineering/validation-benchmarks` at `581eb1e` (2025-05-28) with
  that paper's `benchmark-repair/benchmark-state.patch` applied (335 files). The
  patch only applies cleanly to that commit, not to XBOW HEAD.
- Agent CLI: OpenCode **1.17.7**, the version recorded in the paper's run notes. The
  installed OpenCode 2.x removed `run --dir` and `--dangerously-skip-permissions`,
  so the paper's command line does not parse there.

## Changes to the harness

1. `examples/opencode_cli_agent.py` — `_write_reasoning_config` now also declares a
   non-builtin OpenAI-compatible provider, driven entirely by env vars:
   `OPENCODE_PROVIDER_NPM`, `OPENCODE_PROVIDER_BASE_URL`,
   `OPENCODE_PROVIDER_API_KEY_ENV`, `OPENCODE_MODEL_CONTEXT_LIMIT`,
   `OPENCODE_MODEL_OUTPUT_LIMIT`, `OPENCODE_SEND_REASONING_EFFORT`.
   The output limit matters: a reasoning model that hits a small default truncates
   mid-thought and never emits a final answer.
2. `examples/sandbox.py` — the bwrap jail `--tmpfs /run`, which hides
   systemd-resolved's varlink socket. On Ubuntu/Pop!_OS glibc resolves through
   `nss-resolve`, so *every* DNS lookup inside the jail failed and the CLI reported
   "cannot connect to API", produced zero tokens, and burned its null-run retries.
   The jail now binds `/run/systemd/resolve` back in (2 lines).
3. `scripts/run_morph_sweep.py` — parallel driver with resume. The harness runs one
   benchmark at a time by design (one compose project per task); this runs N
   concurrently and skips `(benchmark, agent)` pairs that already have a scored
   result.
4. `scripts/summarize_morph_sweep.py` — per-task outcome and token accounting.
5. `configs/agents.morph.json` — the two Morph arms. Provider `morph`, model ids
   `morph-kimik3` and `morph-kimik3-abliterated`, reasoning effort `medium`,
   context/output limits 262144/32768. Prices are zeroed and the dollar cap set high
   because billing is internal; the 3600 s wall clock is the real stopping guard.

Everything else — prompt, native OpenCode toolset, bwrap jail, fresh per-run flag,
exact-match scoring, 3600 s timeout — is untouched, so the two arms differ only in
model weights.

## Environment

- `~/xbow-tools/opencode` holds `opencode-ai@1.17.7` from npm.
  `OPENCODE_BIN_DIR` must point at its `node_modules/.bin`, because the wrapper
  derives the jail's read-only tool tree from that path.
- `~/xbow/run.env` (mode 600) exports `MORPH_API_KEY`; the harness does not read
  `.env` files automatically.
- `~/xbow/vb` is the repaired XBOW checkout (the harness expects the repo root, with
  `benchmarks/` inside).

## Second arm (DeepSeek V4.1 Flash abliterated)

Same harness and config shape with `configs/agents.dsv41.json` (identical except the model
id and its 1M/64k limits). One extra requirement: `morph/morph-dsv41flash-abliterated` must be
registered in the harness's model config before any run, or every task dies instantly.

## Known infrastructure limits

- `mysql:5.7.15` (13 benchmarks) fails to build with
  `failed to solve: failed to load cache key: content digest : not found` when
  several stacks build it concurrently on a cold containerd store. Pre-pulling it
  serially before the sweep avoids it.
- Image builds are network-bound. Pre-build before a wide sweep, or keep the worker
  count low on a slow uplink.
- `XBEN-092-24` is reported unsolvable upstream (xbow issue #17); the released
  repair patch does not touch the file at fault.
- The public suite is saturated and called historical by XBOW. The ablation
  comparison between the two arms is the meaningful result here, not the absolute
  score.
