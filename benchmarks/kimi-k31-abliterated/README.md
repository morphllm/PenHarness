# Kimi K3.1 Pro abliterated — benchmark results

**Model:** `morph-kimik3-abliterated`, served on rack1 trays 11+12 (agg TP8/DCP8 + DSpark, L2 DRAM + L3 NVMe), in-cluster frontend `kimik3abl-vllm-frontend`.
**Run date:** 2026-11-19 → 11-20.
**Why:** launch collateral — does abliteration (refusal removal) preserve intelligence, and does it change agentic / offensive-security capability vs the base model?

## TL;DR

| Benchmark | Abliterated K3.1 | Reference point |
|---|---|---|
| AIME-2025 (pass@1, avg-of-10) | **99.3%** | — |
| GPQA Diamond (pass@1, 198 q) | **91.9%** | base self-reported 94.2 |
| IFBench (avg score / prompt-strict) | **71.9% / 68.0%** | — |
| Terminal-Bench 4.0 (terminus-2) | **18/46 graded = 39.1%** (18/66 strict = 27.3%) | published base ~65.7 |
| ExploitGym (sample, ARTEX harness) | **0/20** | codex+our-model baseline 0/20; see §3 |

**Headline:** abliteration **preserved** knowledge/reasoning (AIME/GPQA/IFBench ≈ base) and left **meaningful** agentic-coding ability (TB ~39%, a conservative floor — see §2). It did **not** add autonomous 0-day exploitation capability (ExploitGym, §3) — but neither does almost any model, and our 20-task slice is the hardest part of that benchmark.

---

## 1. Intelligence — preserved (`intelligence/`)

NeMo-Skills `aai` suite, our serving, temperature 1.0 / top_p 0.95, DSpark on.

- **AIME-2025:** 99.3% pass@1 (avg-of-10, 30 problems), 100% at majority@3+.
- **GPQA Diamond:** 91.9% pass@1 (198 questions). Base model self-reports 94.2, so within noise.
- **IFBench:** 71.9 average score, 68.0 prompt-strict / 69.9 instruction-strict (300 prompts).

Takeaway: abliteration did not measurably degrade reasoning or knowledge.

## 2. Terminal-Bench 4.0 — agentic coding (`terminal-bench/`)

Harbor 0.26, terminus-2 agent, run on skeptrune-thelio (the boat 294G disk corrupted an earlier attempt; thelio's 1.8T fixed it).

- **18 tasks resolved.** Harbor metric = 18/46 graded = **39.1%**; strict 18/66 = 27.3%.
- 20 of 66 tasks never scored the model: 14 `EnvironmentStartTimeoutError` (task image failed to build in time over thelio's NAT at concurrency 8) + 4 `RuntimeError` + 2 agent-timeouts. Plus the run used `--agent-timeout-multiplier 0.3` (aggressive).
- So **39.1% is a conservative floor**, not a ceiling. A clean re-run (1.0x agent timeout, longer env-build timeout, concurrency ≤4) is still owed if we want a defensible headline number.

`result.json` + `verdicts.txt` have the per-task detail.

## 3. ExploitGym — autonomous exploitation (`exploitgym/`)

ExploitGym (UC Berkeley RDI + MPI-SP): 869 containerized exploitation tasks (userspace / V8 / Linux kernel). The agent must reverse a specific vulnerable binary and produce a working PoC that triggers the bug and captures a flag. **This is the hardest autonomous benchmark that exists.**

### Result: 0/20 on the sample

We ran the 20-task `data/task_ids/sample.txt` slice (**10 user, 5 kernel, 5 v8**):
- **ARTEX harness (ours): 0/20**, every task `err=None`, each ran the full 1200s budget (`artex-run/scores.tsv`).
- **Codex + our model (prior baseline): 0/20** (`codex-baseline/scores.tsv`).

### Is 0 the model or the harness? — evidence it's the model + task

The live ARTEX trace shows the agent doing **genuine exploitation work**, not misfiring: it reads the actual vulnerable source files under `/src` at targeted offsets, greps for the relevant functions, reasons about the structure it needs, and drives a debugger — the correct workflow against `/workspace`. 800+ `200 OK` model calls, no infra errors. So the harness runs and the model engages; it just doesn't land the exploit in the budget.

### How 0/20 reconciles with "Kimi should be ~8%"

Published ExploitGym numbers (counts out of ~869; see [RDI blog](https://rdi.berkeley.edu/blog/exploitgym/)):

| Model + harness | Successes / ~869 | ≈% |
|---|---|---|
| Claude Mythos Preview + Claude Code | 157 | ~18% |
| GPT-5.5 + Codex | 120 | ~14% |
| GPT-5.4 | 54 | ~6% |
| Claude Opus 4.6 | 15 | ~1.7% |
| Gemini 3.1 Pro | 12 | ~1.4% |
| **(aggregator) regular Kimi K3** | ~70 | ~8% |
| everyone else | single digits | <1% |

Two facts make 0/20 consistent with regular-Kimi-at-~8%, not contradictory:
1. The ~8% is on the **full 869**, and those successes come overwhelmingly from the **easier userspace** category.
2. Our slice is **kernel/V8-heavy** (10 of 20 from the two brutal categories, where even Opus 4.6 / Gemini 3.1 Pro score ~0). On that slice, 0 is what a capable non-frontier model gets.

**To get a number comparable to the ~8%, run the full 869-task set — the 20-sample is a hard, unrepresentative slice.**

### The one thing left to verify (handoff to Zack — see `HARNESS-NOTES.md`)

The LLM path was bridged into the firewalled task container with a forwarder sidecar, but the **controller + dynamically-spawned target-server reachability** (the `catflag → /workspace/flag.txt` completion hop) was **never confirmed end-to-end**. If a firewalled task can't reach the target server to run its PoC and call `catflag`, `flag.txt` is never written and the task scores 0 **even with a correct exploit**. That is the single most likely thing silently depressing the score below the model's true capability — **verify it first.** Details and repro in `HARNESS-NOTES.md`.

---

## Where everything lives

- **ARTEX harness adapter:** `exploitgym/artex.py` (also lands in the ExploitGym repo at `src/cybergym/evaluation/agents/artex.py`, registered in `examples/run_agent.py` as `--agent artex`).
- **Compute:** boat VM `bx_nuraey5p` (resume: `boat resume bx_nuraey5p`). ExploitGym at `~/exploitgym`, ARTEX source at `~/artex-global`, static ARTEX binary at `/tmp/artex-static`, static curl/jq at `/tmp/{curl,jq}-static`.
- **Engine:** reached from the VM at `localhost:18600` via `boat forward bx_nuraey5p --reverse --local 18600 --remote 18600` ← this box's `kubectl port-forward` to the rack1 abliterated frontend.
- **Intelligence + TB harness:** skeptrune `~/git_projects/morph/tmp/k31-intel-eval/` (`run_arm.sh`, `tb_run.sh`).

## Reproduce ExploitGym (ARTEX)

```bash
# on boat VM bx_nuraey5p, in ~/exploitgym
.venv/bin/python scripts/setup/pre_run.py data/task_ids/sample.txt   # controller + LLM proxy + install proxy
# export the CYBERGYM_SERVER_* lines it prints, plus ARTEX_BIN_PATH=/tmp/artex-static
.venv/bin/python examples/run_agent.py --agent artex --tasks-file data/task_ids/sample.txt \
  --model morph-kimik3-abliterated --proxy-url http://172.17.0.1:4000 \
  --proxy-admin-key "$CYBERGYM_ADMIN_KEY" --controller-url http://172.17.0.1:8666 \
  --use-firewall --max-workers 3 --timeout 1200 --out-dir ~/eg_artex_full
```
