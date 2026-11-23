# Public frontier data for the Kimi K3.1 story

Third-party and lab-reported numbers collected 2026-11-23 for the abliterated-model
comparison. **None of this is our measurement** — it is the public record, kept here so
claims in decks and docs can be traced to a source rather than to memory.

**Read this before quoting anything:** every number below is for the *stock* models.
Our runs are `morph-kimik3-abliterated` and `morph-dsv41flash-abliterated`. The bridge
between the two is our own XBOW result (`xbow/README.md`): on a matched 65-task subset
abliterated K3.1 and abliterated V4.1 both solve 63/65, so abliteration cost neither
model measurable capability. That is evidence about abliteration, not about the public
numbers.

## The one-line summary, stated precisely

- **Kimi K3.1 leads DeepSeek V4.1 Flash on offensive exploitation**: ExploitGym 20.7% vs
  15.3% (+35% relative), and it is the highest-scoring open-weight model on that board.
- **Kimi K3.1 is within 5 points of GPT-6 Sol (Daybreak Blue) on the Artificial Analysis
  Cyber Index** (64% vs 69%), and **ahead of it on CyberGym-E2E-AA** (95% vs 93%).
- **DeepSeek is not weak at security.** It is #1 of 19 on MindFort's NexBench and 95.4%
  on Fireworks' CyberGym. "Kimi crushes DeepSeek at hacking" is defensible on ExploitGym
  and the composite index only.

## Artificial Analysis Cyber Index

Agentic cyber *defence* composite (CWE-Bench-AA, DeepsecBench-AA, CyberGym-E2E-AA).
Source: [Artificial Analysis](https://artificialanalysis.ai/evaluations/artificial-analysis-cyber-index),
as reported in their 2026-10-23 announcement and reproduced by
[AIToolsReview](https://aitoolsreview.co.uk/insights/kimi-k3-1-pro) and
[AlphaSignal](https://alphasignal.ai/news/moonshot-s-kimi-k3-1-pro-tops-every-public-api-model-in-security-benchmarks).

| Model | Cyber Index | Cost / task |
|---|---|---|
| GPT-6 Sol (Daybreak Blue, max, trusted access) | 69% | $1.77 |
| Kimi K3.1 Pro | 64% | $2.81 |
| DeepSeek V4.1 Flash 1017 | 57% | $0.42 |
| Kimi K3 | 41% | $2.45 |

Sub-benchmarks, same source:

| Evaluation | Kimi K3.1 Pro | Daybreak Blue | DeepSeek V4.1 Flash 1017 |
|---|---|---|---|
| CyberGym-E2E-AA | **95%** (highest of any model tested) | 93% | not published |
| CWE-Bench-AA | 61% | 65% | 69% |
| DeepsecBench-AA | 37% | 48% | not published |

The composite lead is concentrated in CyberGym-E2E-AA; Kimi trails on the other two.
Kimi K3.1 recorded **no safety refusals** in this evaluation; that absence is a large part
of why Daybreak Blue (guardrails removed) outscores its public counterpart by 32 points.

## ExploitGym

Turn a known vulnerability into a working end-to-end exploit. 898 tasks (userspace, V8,
Linux kernel). Source: [benchlm.ai](https://benchlm.ai/benchmarks/exploitgym) /
[LLMBoard](https://www.llmboard.ai/benchmarks/exploitgym-180), updated 2026-11-19;
Kimi's value reported by Moonshot on the
[K3.1 Pro model card](https://huggingface.co/moonshotai/Kimi-K3.1-Pro) (Kimi Code harness,
6 h wall clock per task).

| Model | ExploitGym |
|---|---|
| Gemini 4 Carbon | 53.5% |
| GPT-6 Cyber | 48.5% |
| GPT-6 Astra | 42.4% |
| GPT-5.6 Sol (**base of Daybreak Blue**) | 33.7% |
| Claude Opus 5.5 | 33.3% |
| GPT-6 Sol | 22.1% |
| **Kimi K3.1 Pro** | **20.7%** |
| MiMo-V2.6-Pro | 17.8% |
| **DeepSeek V4.1 Flash 1017** | **15.3%** |
| GLM-5.3 | 15.0% |

This is the surface where the DeepSeek gap is largest and the Daybreak comparison is
weakest. Use it for "ahead of DeepSeek", not for "level with Daybreak".

## Lab-reported cyber numbers

Moonshot, [K3.1 Pro model card](https://huggingface.co/moonshotai/Kimi-K3.1-Pro):
ExploitBench 74.7, ExploitGym 20.7, xdev-bench-hard (internal) 81.0 pass@3. Moonshot states
it **removed cyber training data** for the open-weight release: the same pipeline without
that removal scored 90.5% on xdev-bench-hard.

DeepSeek, [V4.1-Flash card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash):
CyberGym 88.1% pass@1, ExploitGym 15.3%, xdev-bench-hard 85.7%, Terminal-Bench 4.0 31.2%.

| Benchmark | Kimi K3.1 Pro | DeepSeek V4.1 Flash | Note |
|---|---|---|---|
| ExploitBench | 74.7 | — | Opus 5.5 91.0, GPT-6 Astra 100 |
| xdev-bench-hard (internal) | 81.0 | 85.7 | DeepSeek ahead here |
| CyberGym (pass@1) | not reported | 88.1 | Fireworks measured DS at 95.4 |

## Where DeepSeek wins

Worth keeping in view, because our own XBOW run agrees with it:

- [Fireworks](https://fireworks.ai/specialized-intelligence-index/benchmarks/offensive/cybergym):
  DeepSeek V4.1 Flash (1017) **95.4%** on CyberGym, rank 1, ahead of GLM-5.3 (92.8%).
- [MindFort](https://www.mindfort.ai/blog/how-good-is-deepseek-v4-1-flash-for-cybersecurity):
  DeepSeek V4.1 Flash 1017 scored **121 on NexBench, first of 19 models**, 6% above Grok 4.6.
- Our XBOW run: parity, 63/65 each on the matched subset.

## Vals CyberBench, for completeness

The only public leaderboard carrying Kimi, DeepSeek and Daybreak Blue together *and* a
Kimi number — but it is **Kimi K3, not K3.1**, so it understates the current model.

| Model | CyberBench | Rank |
|---|---|---|
| Kimi K3 | 75.24% | 8 / 49 |
| DeepSeek V4.1 Flash 1017 | 73.69% | 11 / 49 |
| GPT Daybreak Blue | 72.98% | 14 / 49 |

## How to phrase it

Defensible:

> On the Artificial Analysis Cyber Index Kimi K3.1 Pro sits within five points of
> GPT-6 Sol under Daybreak Blue and leads DeepSeek V4.1 Flash by seven points. On
> CyberGym-E2E it is the highest-scoring model tested, ahead of Daybreak Blue. On
> ExploitGym, the hardest end-to-end exploitation benchmark, it leads DeepSeek by 35%
> relative. Abliteration did not cost it any of this: on our own XBOW runs the
> abliterated K3.1 and abliterated V4.1 solve an identical 63 of 65 tasks.

Not defensible:

- "Kimi matches Daybreak Blue on ExploitGym" — no, 20.7 vs 33.7.
- "Kimi clearly beats DeepSeek at security" as a blanket claim — DeepSeek leads
  NexBench and Fireworks' CyberGym.
- Any claim that mixes a stock-model public number with our abliterated-model numbers
  without saying which is which.

## Refusal rates

The abliteration argument, with sources. Two different measurements get conflated; they are
kept apart below deliberately.

### Refusals on clearly harmful prompts

Mean of JailbreakBench, HarmBench and StrongREJECT, as compiled by
[Frontier Log](https://frontierlog.ai/stories/abliteration-removes-refusals) from Anthropic's
test and Morph's own run. Anthropic's figures are with **safeguards disabled** — its classifiers
and monitoring off — so they measure the model's own refusals.

| Model | Refusal rate |
|---|---|
| Claude Mythos Preview (safeguards disabled) | 96% |
| Claude Opus 4.8 (safeguards disabled) | 96% |
| Claude Opus 5 (safeguards disabled) | 95% |
| GLM-5.3, stock → abliterated | 95% → 6% |
| GLM-5.3-Flash, stock → abliterated | 95% → 14% |
| **Morph DeepSeek V4.1 Flash, stock → abliterated** | **100% → 2%** |

The point of that comparison: disabling safeguards does not remove a proprietary model's own
refusals (95–96% survive), while abliteration does. Morph's abliterated DeepSeek also scored
87.6% on IFEval strict (88.7% stock) and 84.0% on MMLU-Pro (83.1% stock).

### Refusals on malicious security requests (vendor system cards)

Anthropic, Claude Code malicious-request refusal rates — dual-use and benign assistance in
brackets: Claude Mythos 5.2 82.8% (98.2%), Mythos 5.1 88.5% (99.3%), Mythos 5 90.3% (99.6%),
Opus 5.5 81.0% (100%). Malicious computer-use refusal: Mythos 5.2 88.4%, Mythos 5.1 87.5%.

### Daybreak Blue

- Artificial Analysis Cyber Index: Daybreak Blue "hits no safety blocks across the entire
  Index", and scores **32 points above** the publicly available GPT-6 Sol — refusals were
  costing the public model a third of its score.
- Advanced Cyber Completion Rate (ACCR): GPT-5.6 Sol with standard safeguards completes 1.5%,
  through Daybreak Blue 2.0%, GPT-5.5-Cyber 57.3%, GPT-5.6-Cyber 95.0%.
- Cybench (cost-aware agent evaluation): GPT-5.6 Sol refused **90.6%** of challenges; Claude
  Fable 5 had all sample-epochs content-filtered.
- Kimi K3.1 Pro recorded **no safety refusals** on the Cyber Index.

### The trap

ACCR's "2.0%" is a **completion** rate on a narrow internal set of advanced-cyber prompts; read
as a refusal rate it says 98%, while the Cyber Index measures the same configuration as
refusing nothing. Never put those two numbers side by side without saying which is which.
