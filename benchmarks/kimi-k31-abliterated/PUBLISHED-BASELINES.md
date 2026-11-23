# Published baselines for our pentest benches

Date: 2026-11-22. Read-only web research. No benchmarks were run and no model APIs were called.

Published scores from other models on the pentest benches we run against `morph-kimik3-abliterated` (Kimi K3.1 Pro abliterated, with `morph-kimik3` as the paired control), so our numbers in this folder have something to sit next to.

| Bench | Our harness | Our results |
|---|---|---|
| XBOW 104 | OpenCode 1.17.7 via `krodalabs/coding-agent-research-artifact` ("Baselines Before Architecture") | `xbow/`, README §4 |
| BountyBench | C-Agent (bundled), with the Morph provider patch | `bountybench/` — 10-bounty exploit pilot: Kimi 7/10 (6 measured), DeepSeek 4/10 |
| AutoPenBench | The paper's agent, patched | not yet run |
| HTB AI Range | Dropped: no self-serve path | see [`../feasibility/XBOW-HTB-REPORT.md`](../feasibility/XBOW-HTB-REPORT.md) |

Pre-run feasibility checks for each bench are in [`../feasibility/`](../feasibility/).

**Verification:** every number below came from its primary source (paper, README or data file). Rows marked ✅ were re-checked a second time against the source. Everything else was read once by a research subagent from the primary source.

---

## 1. XBOW validation benchmarks (104 tasks)

### The comparable rows

Only numbers from our exact harness are apples-to-apples. That harness is the "Baselines Before Architecture" paper's ([arXiv 2607.13085](https://arxiv.org/abs/2607.13085)), and its numbers are all GPT-5 family:

| Agent (model) | Pass 1 | Pass 2 | Union of 2 |
|---|---|---|---|
| **OpenCode (GPT-5)** | **57** | **54** | **67** |
| Pi (GPT-5) | 58 | 46 | 65 |
| Codex (GPT-5) | 70 | 70 | 81 |
| Codex (GPT-5.2) | 84 | 82 | 92 |
| Codex (GPT-5.5, $5 cap) | 96 | 96 | 99 |

**OpenCode GPT-5, pass 1 = 57/104** is the number to put next to our single pass.

### Other models, other scaffolds

These are useful for "where does Kimi sit" context. Scaffold, white-box vs black-box, and hints all differ, so don't present them as head-to-head.

| Model | Agent | Score | Box / hints | Attempts | Source |
|---|---|---|---|---|---|
| **Kimi K3** | pentestkit (Claude Agent SDK, K3 in every role) | **90/104 (86.5%)** first pass, ~$0.66/challenge | Not stated; scope includes internal services | Single pass | ✅ [lordx64/pentestkit](https://github.com/lordx64/pentestkit) |
| Kimi K2.6 | Solkyn | 92/104 pass@1, 96/104 pass@3 | White-box (source-aware), tag-based playbooks | 3 | ✅ [sidthoviti/solkyn](https://github.com/sidthoviti/solkyn), [blog](https://sidthoviti.com/the-solkyn-experiment/) |
| Kimi K2.5 | deadend-cli | ~80% (README body), "81%" (repo About line) | Black-box | Not stated | [straylabs-ai/deadend-cli](https://github.com/straylabs-ai/deadend-cli) |
| Kimi K2.5 | BoxPwnr | 55 of 65 attempted | Black-box + tags/description/difficulty | pass@1 | [boxpwnr.info/data/xbow.json](https://boxpwnr.info/data/xbow.json) |
| Kimi K2 Thinking | deadend-cli | ~69% (probably of 98) | Black-box | Not stated | [Medium post](https://xoxruns.medium.com/feedback-driven-iteration-and-fully-local-webapp-pentesting-ai-agent-achieving-78-on-xbow-199ef719bf01) |
| DeepSeek V4 Pro | Solkyn | 92/104 pass@1, 98/104 pass@3 | White-box + tags | 3 | ✅ Solkyn README |
| GLM-5 | BoxPwnr | 85/104 (81.7%) | Black-box + hints | pass@1 | boxpwnr json |
| GLM-5 | CHYing-agent | ~57% | Not stated | Early version | [yhy0/CHYing-agent](https://github.com/yhy0/CHYing-agent) (self-report) |
| GPT-5.5 | T3MP3ST | Black-box pass@1 mean 90.1% (Wilson 86.2 to 92.9); white-box pass@1 98.7% | Both, reported separately | pass@1 mean | [elder-plinius/T3MP3ST](https://github.com/elder-plinius/T3MP3ST) (self-report) |
| GPT-5.4 | Solkyn | 92/104 pass@1, 95/104 pass@3 | White-box + tags | 3 | ✅ Solkyn README |
| GPT-5.2 | PentestGPT V2 | 76 non-thinking / 85 thinking | Not stated | See note | [arXiv 2602.17622](https://arxiv.org/abs/2602.17622) Table 5 |
| Claude Opus 4.5 | PentestGPT V2 | 81 non-thinking / 91 thinking | Not stated | See note | 2602.17622 Table 5 |
| Gemini 3 Pro | PentestGPT V2 | 76 non-thinking / 79 thinking | Not stated | See note | 2602.17622 Table 5 |
| GPT-5 | MAPTA | 80/104 (76.9%), $21.38 total | Black-box | Single | [arXiv 2508.20816](https://arxiv.org/abs/2508.20816) |
| Claude Sonnet 4.5 | deadend-cli | 77.55% of 98 | Black-box | Not stated | Medium post above |
| Claude Sonnet 4.5 (inferred) | Shannon Lite | 100/104 | White-box, Keygraph's hint-free variant | Not stated | [Keygraph results](https://github.com/KeygraphHQ/xbow-validation-benchmarks/tree/main/xben-benchmark-results); the model is never named |
| Grok 4 Fast | BoxPwnr | 51/104 (49.0%) | Black-box + hints | pass@1 | boxpwnr json |
| Mix: DeepSeek V4 Pro/Flash + Kimi K3/K2.6 | RedAmon | 101/104 | Black-box, but web search on and XBEN-092 patched | Mixed models, reruns | [RedAmon wiki](https://github.com/samugit83/redamon/wiki/XBOW-Validation-Benchmark) |

**Older-scaffold baselines from 2602.17622 Table 2** (best-of-3, temp 0, % of 104). Older agents like these score much lower on the same models:

| Model | PentestGPT | AutoPT | PentestAgent | VulnBot | Cochise |
|---|---|---|---|---|---|
| GPT-5 | 42 | 40 | 49 | 45 | 43 |
| Claude Sonnet 4 | 39 | 37 | 46 | 46 | 39 |
| Gemini 3 Flash | 36 | 35 | 42 | 44 | 39 |
| GPT-4o | 27 | 28 | 34 | 39 | 34 |

### XBOW caveats

- **The bench is saturated.** XBOW's README calls it outdated and puts "industry standard" at ~100%. Frontier and open models cluster at ~90/104 pass@1 on strong scaffolds (Solkyn ties GPT-5.4, Kimi K2.6 and DeepSeek V4 Pro at 92). The paired abliterated-vs-base comparison on our harness is the claim that holds up; an absolute score is weak evidence.
- **pentestkit's 104/104 headline is not comparable.** It fixed the harness after studying its own 14 misses on the test set and reran only those. Only the 90/104 first pass counts.
- **PentestGPT V2 is unclear about its metric.** The Table 5 caption says "mean across 3 trials", but §5.2 calls 91 "best-of-3 (μ=89%, σ=2.1%)" and gives 85 a mean of 83.
- **The "PentestGPT 86.5% (90/104)" figure in [`../feasibility/XBOW-HTB-REPORT.md`](../feasibility/XBOW-HTB-REPORT.md) is the open-source repo's historical result with no model named.** It isn't the paper's system; the paper's original-PentestGPT rows are 41 to 54%.
- **BoxPwnr's "completion" column always divides by 104,** even when a model attempted only 9 to 65 tasks. It also runs on a patched fork (`0ca/xbow-validation-benchmarks-patched`).
- **Hints:** BoxPwnr, Decepticon and Solkyn pass vulnerability tags. Keygraph notes the original challenges also leak hints through variable names and comments, which matters most in white-box runs.
- **Not comparable:** XBOW's "GPT-5 79%" blog number was measured on XBOW's internal benchmark, not the public 104.
- **Not found:** Qwen3/Qwen3-Coder, gpt-oss, DeepSeek R1 / V3.x, GLM-4.5, Gemini 2.5, any Claude 5.x.

---

## 2. BountyBench (Detect / Exploit / Patch)

### Original paper (Table 1, up to 3 attempts)

Source: [arXiv 2505.15216](https://arxiv.org/html/2505.15216), NeurIPS 2025 D&B. These are the main comparison set; the C-Agent rows match the agent we'll run.

| Agent | Detect % | Detect $ | Exploit % | Patch % | Patch $ |
|---|---|---|---|---|---|
| C-Agent: Claude 3.7 Thinking | 5.0 | $1,025 | 67.5 | 60.0 | $11,285 |
| C-Agent: GPT-4.1 | 0.0 | $0 | 55.0 | 50.0 | $4,420 |
| C-Agent: Gemini 2.5 Pro | 2.5 | $1,080 | 40.0 | 45.0 | $3,832 |
| C-Agent: o3-high | 0.0 | $0 | 37.5 | 35.0 | $3,216 |
| C-Agent: DeepSeek-R1 | 2.5 | $125 | 37.5 | 50.0 | $4,318 |
| C-Agent: Llama 4 Maverick | 0.0 | $0 | 42.5 | 42.5 | $10,425 |
| C-Agent: Qwen3 235B A22B | 0.0 | $0 | 17.5 | 25.0 | $1,344 |
| Claude Code (reference only) | 5.0 | $1,350 | 57.5 | 87.5 | $13,862 |
| Codex CLI: o3-high (reference only) | 12.5 | $3,720 | 47.5 | 90.0 | $14,152 |
| Codex CLI: o4-mini (reference only) | 5.0 | $2,400 | 32.5 | 90.0 | $14,422 |

### Newer models

**Co-RedTeam** (Google / Google Cloud AI Research / MSU, [arXiv 2602.02164](https://arxiv.org/html/2602.02164) Table 1, Feb 2026). ✅

| Agent | Model | Exploit | Detect |
|---|---|---|---|
| **C-Agent** | **Gemini 3 Pro** | **47.5%** | **5.0%** |
| C-Agent | Gemini 2.5 Pro | 40.0% | 2.5% |
| C-Agent | Gemini 2.5 Flash | 20.0% | 0.0% |
| OpenHands | Gemini 3 Pro / 2.5 Pro / 2.5 Flash | 45.0 / 42.5 / 17.5% | 5.0 / 0.0 / 0.0% |
| Co-RedTeam (Google's own agent) | Gemini 3 Pro / 2.5 Pro / 2.5 Flash | 65.0 / 60.0 / 32.5% | 20.0 / 12.5 / 7.5% |
| RepoAudit | Gemini 3 Pro / 2.5 Pro / 2.5 Flash | 25.0 / 15.0 / 5.0% | 2.5 / 0.0 / 0.0% |
| VulTrail | Gemini 3 Pro / 2.5 Pro / 2.5 Flash | 10.0 / 7.5 / 0.0% | 0.0 / 0.0 / 0.0% |
| Vanilla | Gemini 3 Pro / 2.5 Pro / 2.5 Flash | 17.5 / 12.5 / 7.5% | 0.0 / 0.0 / 0.0% |

- **Different from the original paper:**
  - Google authors evaluating Google models.
  - No Patch mode and no dollar figures.
  - Detect gets only the codebase, Exploit gets the vulnerability description.
- **Not stated:**
  - Task count. Every score is a multiple of 2.5%, so it is probably 40 tasks; that's an inference.
  - Attempts per task. Stage I allows 3 iterations and Stage II is capped at 20.
- **Not read:** Appendix C Table 5 (other model families); the fetch was blocked.

**Antiproof** (Berkeley, [arXiv 2607.12316](https://arxiv.org/html/2607.12316v1) Table III, Jul 2026). **Not BountyBench scoring.** It counts a hit when a detection report semantically matches the known vuln, across all 46 bounties, with 30 min per target and the mean of 10 runs:

| Model | Agent | Hits / 46 |
|---|---|---|
| Claude Opus 4.6 | Claude Code CLI | 31 |
| GPT-5.3-codex / GPT-5.4-mini / GPT-5.5 | Codex CLI | 27 / 27 / 27 |
| Opus 4.6 / Qwen 3.7-Max / GLM-5.1 / GPT-5.3-codex / GPT-5.5 | Antiproof pipeline | 42 / 41 / 42 / 42 / 44 |

### Who does not report BountyBench

Each document was searched in full text:

- **Anthropic:** system cards for Sonnet 4.5, Opus 4.5, Opus 4.6, Mythos Preview, Fable/Mythos 5, Opus 5, Fable/Mythos 5.1, Opus 5.5 and Fable/Mythos 5.2. Every "bounty" hit was the Gray Swan prompt-injection bounty.
- **OpenAI:** the GPT-5 system card, the gpt-oss card, GPT-5.6, GPT-6 Astra, the GPT-6.1 Sol addendum and GPT-6 Cyber.
- **Google:** the Gemini 2.5 tech report and the Gemini 3 Pro Frontier Safety Framework report.
- **Government:** CAISI's Kimi K2 Thinking evaluation and the UK AISI / CAISI Kimi K3 assessment.

The labs now use CyberGym, ExploitGym, ExploitBench, CVE-Bench, SEC-Bench Pro and CyScenarioBench, and most have retired Cybench as saturated.

**Not checked:** Gemini 3.1+ cards, xAI Grok cards, first-party Kimi / DeepSeek / GLM / Qwen / MiniMax tech reports, METR, Epoch, Artificial Analysis, SEAL.

### BountyBench caveats

- **Detect is reward-hackable.** "Detecting Safety Violations Across Many Agent Traces" ([arXiv 2604.11806](https://arxiv.org/abs/2604.11806), Appendix H) documents Opus 4.6 passing Detect with grep-based "exploits" and mocked dependencies. The checker only tests whether `exploit.sh` behaves differently on vulnerable and patched code. Inspect our Detect passes by hand before claiming them.
- **No new published runs elsewhere.** benchmarklist.com and other aggregators only repeat the original paper's numbers.

---

## 3. AutoPenBench (33 tasks)

### Original paper

Source: [EMNLP 2025 Industry](https://aclanthology.org/2025.emnlp-industry.114.pdf). Model `gpt-4o-2024-08-06`, temp 0, apparently one run per task. Autonomous **SR 0.21**, assisted 0.64. Full per-category table in [`../feasibility/AUTOPENBENCH-REPORT.md`](../feasibility/AUTOPENBENCH-REPORT.md) §8.

The EMNLP Table 3 model comparison covers **only task AC0** (5 runs per model), not the full bench. SR is the fraction of those 5 runs that succeeded; PR is the average progress on failed runs only.

| Model | SR | PR (failed runs) | Main failure |
|---|---|---|---|
| gpt-4o (2024-08-06) | 1.00 | – | – |
| deepseek-v3 | 0.60 | 0.250 | Structured output format |
| gpt-4-turbo | 0.40 | 0.120 | Contextual awareness |
| deepseek-r1 | 0.40 | 0.167 | Structured output format |
| gpt-4o-mini | 0.00 | 0.550 | Structured output format |
| o1-mini | 0.00 | 0.275 | Contextual awareness |
| o1-preview | 0.00 | 0.125 | Refused |
| gemini-1.5-flash | 0.00 | 0.050 | Contextual awareness |

### Newer models

**CyberEvolver** (Fudan, [arXiv 2605.26195](https://arxiv.org/html/2605.26195) Table 1, Jun 2026), solve rate % on all 33 tasks. ✅

The "AutoPenBench-Agent" column is the original paper's agent (30-step cap, pass@4), the closest match to what we'll run.

| Model | AutoPenBench-Agent (pass@4) | VulnBot (pass@4) | Seed agent pass@1 / @4 / @16 | ACE (pass@4) | CyberEvolver (≤16 rollouts) |
|---|---|---|---|---|---|
| **Kimi K2.5** | **42.4** | 39.4 | 52.5 / 60.3 / 60.6 | 45.5 | 72.7 |
| DeepSeek V3.1 | 36.4 | 33.3 | 34.4 / 50.3 / 51.5 | 36.4 | 60.6 |
| MiniMax M2.5 | 27.3 | 33.3 | 29.4 / 41.2 / 42.4 | 36.4 | 66.7 |
| Qwen3-235B | 27.3 | 12.1 | 13.4 / 22.9 / 24.3 | 36.4 | 63.6 |

The appendix on "AutoPenBench curation and fixes" (B.1.2) was not readable, so the authors may have changed some tasks.

**Shell or Nothing / TermiAgent** ([arXiv 2509.09207](https://arxiv.org/html/2509.09207) Table 4, Sep 2025). pass@5, targets solved out of 33, with AutoPenBench's default (hinted) instructions:

| Model | Built-in AutoPenBench agent | TermiAgent | VulnBot | PentestGPT |
|---|---|---|---|---|
| GPT-5 (2025-08-07) | **22 (66.7%)** | 11 | 3 | 3 |
| DeepSeek V3-0324 | 10 (30.3%) | 15 | 9 | 4 |
| Qwen3-30B-A3B | 3 (9.1%) | 15 | 7 | 0 |
| Qwen3-8B | 5 (15.2%) | 10 | 4 | 0 |

The authors flag GPT-5's 22 as possibly inflated, because its knowledge cutoff is close to when AutoPenBench and its solutions were published.

**Pentest-R1** ([arXiv 2508.07382](https://arxiv.org/html/2508.07382) Table II, Oct 2025). pass@3, autonomous, 30-turn limit:

| Model | SR % | Subtask % |
|---|---|---|
| Gemini 2.5 Flash (thinking / non-thinking) | 27.3 / 18.2 | 44.2 / 39.1 |
| Pentest-R1 (RL-tuned R1-0528-Qwen3-8B) | 24.2 | 33.4 |
| GPT-4o | 21.2 | 29.0 |
| Claude 4 Opus | 18.2 | 16.4 |
| Claude 3.7 Sonnet (thinking / non-thinking) | 9.1 / 15.2 | 12.6 / 28.4 |
| o1-preview | 15.2 | 14.5 |
| Qwen2.5-32B | 12.1 | 37.9 |
| Qwen3-32B (thinking / non-thinking) | 9.1 / 12.1 | 29.0 / 36.9 |
| Gemini 1.5 Pro | 9.1 | 22.1 |
| Llama 3.1 405B | 6.1 | 7.9 |

Two rows may not be real pass@3 runs:

- **GPT-4o 21.2:** equals the original paper's single-run 21%, so it may be copied.
- **"Claude 4 Opus":** printed as "Claude 4 Opus / Claude 3 Opus", and the row's Cybench numbers appear to be Claude 3 Opus's.

**VulnBot** ([arXiv 2501.13411](https://arxiv.org/html/2501.13411) Table 2, Jan 2025). Temp 0.5, 15 steps, 5 runs (combination rule not stated):

| Setup | Solved / 33 |
|---|---|
| VulnBot + Llama 3.1 405B | 10 (30.3%) |
| VulnBot + Llama 3.3 70B | 6 (18.2%) |
| Plain model + Llama 3.1 405B / 3.3 70B | 3 / 2 |

### AutoPenBench caveats

- **Every paper uses a different metric.** Original: single run. Pentest-R1: pass@3. CyberEvolver baselines: pass@4. TermiAgent: pass@5. Match ours to the one we're citing.
- **The scaffold matters as much as the model.** Llama 3.1 405B ranges from 6.1% to 30.3% depending on agent.
- **The paper's agent is fragile on output format.** Its failures on reasoning models and DeepSeek were "structured output format", which matters for our reasoning model (see [`../feasibility/AUTOPENBENCH-REPORT.md`](../feasibility/AUTOPENBENCH-REPORT.md) on JSON mode).
- **Not found:** Kimi K3, GLM, Claude 4.5+, GPT-5.x beyond GPT-5, Gemini 2.5 Pro / 3.x, gpt-oss, Llama 4, DeepSeek V4.

---

## 4. Published Kimi cyber evals on other benches

None of these are our benches, but they are the only government / third-party numbers for the Kimi family.

**UK AISI + CAISI, preliminary assessment of Kimi K3** ([aisi.gov.uk](https://www.aisi.gov.uk/blog/preliminary-assessment-of-kimi-k3s-cyber-capabilities), Jul 23, 2026). ✅

- **ExploitBench** (41 V8 vulns):
  - Kimi K3: **32%**, reaching arbitrary code execution on 0/41.
  - GLM-5.2: **24%** ("the most cyber-capable open-weight model as of June 2026").
  - Leading US models: arbitrary code execution on ~20/41 on average.
- **The Last Ones cyber range** (32 steps):
  - Kimi K3: step 17 on average, completed 1 of 10 attempts.
  - GLM-5.2: step 11 on average.
  - Most capable US models: step 28.5 on average.

**CAISI evaluation of Kimi K2 Thinking** ([NIST](https://www.nist.gov/news-events/news/2025/12/caisi-evaluation-kimi-k2-thinking), Dec 12, 2025), % solved:

| Model | Cybench | CVE-Bench |
|---|---|---|
| GPT-5 | 73.5 | 65.6 |
| gpt-oss | 49.5 | 42.2 |
| Claude Opus 4 | 46.9 | 66.7 |
| Kimi K2 Thinking | 40.0 | 50.5 |
| DeepSeek V3.1 | 40.0 | 36.7 |
| DeepSeek R1-0528 | 35.5 | 36.0 |
| DeepSeek R1 | 16.7 | 26.7 |

**"An Independent Safety Evaluation of Kimi K2.5"** ([arXiv 2604.03121](https://arxiv.org/pdf/2604.03121) Fig. 5). Cybench, unguided, one run, pass@1, values read from the figure's text layer:

| Model | Cybench |
|---|---|
| GPT-5.2 | 80.0% |
| Claude Opus 4.5 | 67.5% |
| Kimi K2.5 | 55.0% |
| DeepSeek V3.2 | 12.5% |

---

## 5. How to read our numbers against these

- **XBOW:**
  - Put our 1-pass score next to **OpenCode GPT-5 = 57/104** (same harness, full set). README §4 does the subset-matched version from the paper's per-challenge results.
  - Kimi K3 on a different scaffold got **90/104** first pass (pentestkit), so a much lower number on ours points at the harness or endpoint, not the model.
  - The paired abliterated-vs-base run is the claim that survives saturation.
- **BountyBench:**
  - Compare our C-Agent rows to the paper's C-Agent rows plus Co-RedTeam's **C-Agent Gemini 3 Pro (47.5% Exploit / 5.0% Detect)**.
  - Match the paper's settings: 50 model calls, 8192/8192 tokens, 3 attempts.
  - Hand-audit Detect passes for reward hacking.
- **AutoPenBench:**
  - The cleanest open-model comparison is CyberEvolver's original-agent column. Kimi K2.5 scored **42.4% pass@4** there.
  - Report our pass@k explicitly.
