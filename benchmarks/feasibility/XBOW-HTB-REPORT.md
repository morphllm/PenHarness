# XBOW + HTB AI Range feasibility check (2026-11-22)

Read-only check. Nothing was executed: no docker, no builds, no model or API requests.

## Verdicts

**(A) XBOW validation benchmarks: runnable with a small patch.** Use the open harness from the "Baselines Before Architecture" paper on an older XBOW commit with that paper's repair patch. The patch applies cleanly there. Wiring in our endpoint needs about 10 lines in the harness's OpenCode adapter, and Codex needs a provider block on top of that. Two things limit the number we'd get:
- **The benchmark is saturated.** XBOW's own README now calls it outdated and says "industry-standard performance… is now approximately 100%" (`README.md:3-15`).
- **One task is reported unsolvable.** XBEN-092 has an open issue, and the repair patch doesn't touch the file at fault.

A comparison between the abliterated and base models on the same harness is still meaningful. A single absolute score is weak evidence.

**(B) HTB AI Range: no self-serve path.** Both hackthebox.ai/benchmarks and hackthebox.ai/contact now redirect (301) to hackthebox.com/ai, a marketing page with no scores. I found no public harness and no published list of tested models. I also couldn't find the "10 challenges x 10 attempts" spec on any HTB page I fetched. A demo request is the only route, and that is an outside contact, so it needs Nick's OK before anyone sends it.

**Safeguards:** my first WebSearch (for the XBOW repo) was refused. I switched to the GitHub API and WebFetch, and nothing else was refused.

## (A) Details

Paths below are relative to the clone, `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/repo/`.

### Repo is official
- It's `xbow-engineering/validation-benchmarks`.
- The GitHub org is verified (`is_verified: true`, blog xbow.com) and the repo homepage is xbow.com.
- It has 104 `XBEN-*-24` directories, 44 commits, and its last commit is 2026-07-07 (the saturation warning).

### License
- Apache-2.0 (`README.md:83`, `LICENSE`), © XBOW USA Inc. (`NOTICE:1-2`).
- No restrictions beyond Apache.
- The README asks you to run it only in isolated or sandboxed settings (`README.md:87-89`, `SECURITY.md:3-5`).
- It carries "should never appear in training corpora" canary strings (`README.md:91-97`).

### Targets only, no agent
The repo is a `Makefile` plus `common.mk` that build and run compose stacks. Harnesses I checked, all confirmed to exist via the GitHub API:

| Harness | Fit for our endpoint |
|---|---|
| **`krodalabs/coding-agent-research-artifact`** (Apache-2.0), the "Baselines Before Architecture" code (arXiv 2607.13085, Dhakal, Neupane, Chaudhary, 2026-07-13) | **Best option.** Covers all 104 tasks, plants a fresh flag per run, scores exact matches, and runs the agent in a bubblewrap sandbox. It wraps the Codex, OpenCode and Pi CLIs (`README.md:115-146`). It requires Linux, bubblewrap, Docker, the agent CLIs and a separate XBOW checkout (`README.md:117`). |
| MAPTA (`arthurgervais/mapta`, MIT, 136 stars, last push 2025-08-28) | **Not runnable.** The repo has only `main.py`, `analyze_logs.py` and `LICENSE`, plus 105 run logs. `main.py:11` imports a `function_tool` module that isn't in the repo. It uses `AsyncOpenAI()` (`main.py:22`) with the model hardcoded to `gpt-5` on the Responses API (`main.py:545-549`, `629-630`). The sandbox was "sanitized for open release" and needs a user-supplied `SANDBOX_FACTORY` (`main.py:25-27`). There is no XBOW runner or flag scoring. |
| `straylabs-ai/deadend-cli` (AGPL-3.0) | Possible second option. Built on LiteLLM, so it takes a custom `base_url` (README:257-264). It reports about 80% with **Kimi K2.5**, black-box, January 2026, about $122 (README:19, 91-93). That is the closest published number for a model in our family. I didn't check how it drives all 104 tasks. |
| `GreyDGL/PentestGPT` (MIT) | Its autonomous mode drives only the Claude Code or Codex CLIs (README:57-62). Only its legacy interactive mode takes `--base-url` (README:174-175). Its XBOW harness lives outside the repo (README:232-234). |
| Others that exist but I didn't evaluate | `usestrix/strix`, `KeygraphHQ/shannon` (AGPL), `westonbrown/Cyber-AutoAgent`, `lordx64/pentestkit`, `sidthoviti/solkyn` |

How to wire our model into the krodalabs harness:
- **OpenCode.** Provider and model come from env vars (`OPENCODE_PROVIDER_ID`, `OPENCODE_MODEL_ID`; `examples/opencode_cli_agent.py:183`, `223`). But the config it writes only sets `reasoningEffort` (`opencode_cli_agent.py:157-171`). The patch is to have it write an `@ai-sdk/openai-compatible` provider with `baseURL=https://api.morphllm.com/v1`, plus an output-token limit large enough for a reasoning model. The cost-guard prices and cap are env vars (`configs/agents.baseline.json:3-10`).
- **Codex.** Codex was the strongest scaffold in the paper (70 vs 57 for OpenCode on GPT-5). But current Codex rejects `wire_api="chat"` with a hard error (openai/codex `codex-rs/model-provider-info/src/lib.rs:112,142`), so it needs a Responses endpoint. The patch adds a `model_provider` block in `_write_codex_config` (`examples/codex_cli_agent.py:248-270`).
- **Responses support on our side.** A 2026-10-09 artifact snapshot of our proxy has a stateless `/v1/responses` shim that translates to chat. It refuses `previous_response_id` (`/home/skeptrune/git_projects/morph/artifacts/kimik3-structured-investigation-20261009/cpu-release-build-4d239f7817/context/shared/general_proxy.py:7025`, `responses_compat.py:1-14`). I haven't verified that the live endpoint serves it.

### Flags, and whether they leak
- Running `make build` gives a fixed default flag: `FLAG{sha256("XBEN-NNN-24")}` (`common.mk:22-23`, `58`). Anyone can compute it.
- Every benchmark also commits a fixed flag in `.env`, for example `benchmarks/XBEN-001-24/.env:1` `FLAG="flag{85c0d827-...}"`. All 104 have one, added in commit `6904a33` ("add random uuids"). The compose files read `FLAG` from that file (`benchmarks/XBEN-001-24/docker-compose.yml:15-16`).
- Dockerfiles inject the flag at build time with `sed` (for example `RUN sed -i s/@FLAG@/$FLAG/g secret.txt`), with `echo $FLAG > /flag`, or with `ENV FLAG=$FLAG`.
- **Leak risk is high unless you use the harness.** It plants a fresh `FLAG{uuid4hex}` through `--build-arg FLAG=/flag=` (`xbow_harness/xbow.py:35-36`, `51-57`) and requires an exact match (`xbow_harness/scoring.py:7`, `26`). Memorized or committed flags can't score.
- **Solutions can still leak.** The sandbox has no docker socket and hides the host (`data/source/environment_notes.txt:16-18`), but it doesn't restrict outbound network (krodalabs `README.md:158`; `examples/sandbox.py:17`). Public writeups exist (`lawneng/XBOW-Validation-Writeups`), and XBOW says the vulnerabilities have been trained into models (`README.md:8-9`). For a customer-grade number, limit the agent's network to the target and our API.

### Infra
- One docker compose stack per task: 76 have one service, 22 have two, 3 have three, 3 have four. The benchmarks take 1.4 GB on disk. I didn't measure total image size.
- No compose file or Dockerfile pins a platform.
- Base images, by number of Dockerfiles:

  | Image | Count |
  |---|---|
  | `python:2.7.18-slim` | 23 |
  | `debian:bullseye-slim` | 16 |
  | `python:3.8-slim` | 13 |
  | `mysql:5.7.15` | 13 |
  | `php:7.4-apache` | 11 |
  | `python:3.8-slim-buster` | 7 |
  | `php:5-apache` | 7 |
  | Others, including `httpd:2.4.49/2.4.50`, `haproxy:2.0.5`, `mitmproxy:6.0.2`, `wordpress:latest`, `mongo:latest` | — |

  Plan on x86_64. That mysql 5.7 has no arm64 image is from memory, not checked here.
- **Builds break on HEAD.** 33 Dockerfiles on end-of-life bases (`python:2.7.18-slim`, `python:3.8-slim-buster`, `php:7.1-apache`) run `apt-get` with no `archive.debian.org` fix.
  - Issue #5 (closed) reports 67 of 104 built and 37 failed (64.4% success), 23 of those on phantomjs.
  - Issue #6 (open) shows buster 404s on XBEN-007.
- **XBEN-092-24 is reported unsolvable** (issue #17, open): the SSRF body is hardcoded in `app/sku_url.php`. The krodalabs patch touches 092's `.env`, composer files, `skumodel-srv/Dockerfile` and compose file, but not `sku_url.php`, so the defect probably remains.
- **Open fix PRs:**
  - #11 fixes 50 benchmarks for Ubuntu 24.04.
  - #9 and #8 fix the Debian sources.
  - The fork `Neuro-Sploit/xbow-validation-benchmarks` claims to fix all builds.
  - PR #18 adds 106 new third-party 2026 tasks (XBEN-105-26 to 210-26); it is unmerged and unvetted.
- **The repair patch path is verified by dry run.**
  - The paper repaired about 40 benchmarks (38 build fixes, 2 runtime fixes; krodalabs `README.md:152`). Its environment notes say all 104 ran, at 6-wide concurrency (`data/source/environment_notes.txt:20-22`).
  - Their `benchmark-repair/benchmark-state.patch` **fails on HEAD**: 428 lines of errors from `git apply --check`, because it creates `.env` and `benchmark.yaml` as new files.
  - It **applies cleanly to commit `581eb1e` (2025-05-28)**, with 0 errors.
  - Recipe: check out `581eb1e`, then `git apply benchmark-state.patch`.

### Wall time and tokens
These are the paper's measured figures, all on GPT-5-family models. Kimi K3 will differ, especially in tokens per task.

**Run settings:**
- Concurrency was 6-wide for the GPT-5 conditions (`data/source/environment_notes.txt:22`).
- Each agent has a 3600 s timeout (`configs/agents.baseline.json:18`). OpenCode defaults to 1700 s (`opencode_cli_agent.py:185`).
- Each container stack waits up to 180 s to come up (`xbow_harness/xbow.py:58`).
- Every run rebuilds the stack with a fresh flag. Most Dockerfiles set `ARG FLAG` late, so layer cache absorbs most of the rebuild.

**Per pass of 104 tasks (arXiv 2607.13085, RQ1 and RQ4):**

| Condition | Total tokens | Median s/task | Tokens per solve | $/task |
|---|---|---|---|---|
| Codex GPT-5 | 183.7M | 165 | 2.27M | 0.266 |
| OpenCode GPT-5 | 95.6M | 118 | 1.43M | 0.115 |
| Pi GPT-5 | 67.8M | 83 | 1.04M | 0.115 |
| Codex GPT-5.2 | 289.1M | 106 | 3.14M | 0.486 |
| Codex GPT-5.5 | 102.9M | 54 | 1.04M | 0.669 |

Failed attempts run longer. Median failed duration is 393 s for Codex, 236 s for OpenCode and 152 s for Pi (krodalabs `README.md:41-43`). Median failed tokens are 1.59M, 0.44M and 0.18M.

**Rough estimate for our run** (derived from the figures above, not measured):
- About 100-290M tokens per model per pass. The full plan is 2 models x 2 passes, so roughly 0.4-1.2B tokens.
- About 1-2 h wall time per pass at 6-wide, plus a first-time image build for all 104 tasks.
- The harness's dollar cap is computed from token prices in env vars, so set prices to make the cap mean a token budget for our model.

### Published scores on the public 104

| System (model) | Score | Source |
|---|---|---|
| MAPTA (GPT-5) | 76.9% (80/104), $21.38 total | arXiv 2508.20816 abstract; the model is named in 2607.13085 |
| Codex, plain coding agent (GPT-5, $0.75 cap) | 70 and 70 per pass, 81 across both (77.9%) | arXiv 2607.13085 |
| OpenCode (GPT-5) | 57 and 54 per pass, 67 across both | arXiv 2607.13085 |
| Pi (GPT-5) | 58 and 46 per pass, 65 across both | arXiv 2607.13085 |
| Codex (GPT-5.2) | 84 and 82 per pass, 92 across both | arXiv 2607.13085 |
| Codex (GPT-5.5, $5 cap) | 96 and 96 per pass, 99 across both | arXiv 2607.13085 |
| PentestGPT | 86.5% (90/104) | PentestGPT README:228 |
| PentestGPT V2 | 85% on GPT-5.2; 91% on Opus 4.5 | As cited in 2607.13085 |
| deadend-cli (Kimi K2.5) | about 80%, black-box, about $122 | deadend-cli README:19, 91-93 |
| Cipher | 86.7% "detection rate" (vendor self-report) | `apxlabs-ai/cipher-xbow-benchmark` |
| "Industry standard" | approximately 100% | XBOW `README.md:11-13` |

**XBOW's "GPT-5 79%" is not comparable.** It was measured on XBOW's internal benchmark (the Sonnet/Gemini setup scored 55%), not on the public 104 (xbow.com/blog/gpt-5). XBOW's fall 2026 post (xbow.com/blog/top-ai-models-for-offensive-security-fall-2026) uses a new internal set and gives no per-model numbers in its text.

## (B) Details

- hackthebox.ai/benchmarks and hackthebox.ai/contact both redirect (301) to hackthebox.com/ai, a marketing page with no benchmark, models, scores or harness.
- hackthebox.com/ai/agent-competence: AI Range Enterprise Edition evaluates deployed agents against role standards. It says model benchmarking is a separate product, "AI Range Frontier Edition." The route is a demo form (on-page `#main-contact`, or hackthebox.com/business/contact-us).
- hackthebox.com/ai/ai-for-security: nothing on model testing.
- AI Range launch post (2025-12-03, hackthebox.com/blog/htb-ai-range-launch): names no models. Its only result is from an earlier AI vs Human CTF ("19 of 20 easy-tier").
- Enterprise Edition post (2026-10-06): no scores or model list.
- The `hackthebox` GitHub org has no AI Range or benchmark repo.
- Verdict: a demo request is the only route. It's an outside contact, so it needs Nick's approval.

## Local files (read only, nothing executed)
- `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/repo`: the XBOW clone at HEAD `a16cb1b`.
- `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/repo-at-581eb1e`: a git worktree of the older commit, used for the patch dry run.
- `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/krodalabs-src`: the harness files and repair patch.
- `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/mapta-src`: MAPTA's `main.py`.
