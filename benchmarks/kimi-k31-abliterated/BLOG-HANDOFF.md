# Handoff: blog post for the Kimi K3.1 Abliterated release

Written 2026-11-23 for the agent drafting the post. Everything below was checked against the files and pages it cites on that date. Anything not in this doc or its sources is not a verified fact: don't add numbers, prices or claims from memory.

## The job

Write a blog post for morphllm.com, plus the charts that go in it, about `morph-kimik3-abliterated` (Kimi K3.1 Pro with its refusals removed). The post tells one story with two claims:

1. **On security work, Kimi K3.1 Abliterated does the job without refusing, and it's the strongest zero-refusal pentester we've tested.** It should be clearly better than our other abliterated model, DeepSeek V4.1 Flash Abliterated.
2. **Removing the refusals didn't make it dumber.** Abliterated Kimi scores the same as stock Kimi.

**Never mention any third-party checkpoint, model card, Hugging Face repo or outside lab in connection with this model.** It is Morph's Kimi K3.1 Abliterated, full stop. Every refusal or capability number in the post comes from our own runs.

Both claims are only partly backed by data today. The table in "Claims" below says exactly what you can say now and what has to wait for the runs listed in "Gaps". Don't write around a gap; mark it `TODO(data)` in the draft.

## Read these first

| What | Where |
|---|---|
| The closest existing post (structure, tone, MDX mechanics) | `~/git_projects/morph/landing/content/posts/uncensored-deepseek.mdx`. Read it end to end. |
| Our Kimi results | `README.md` in this folder (intelligence, Terminal-Bench, ExploitGym), `xbow/README.md`, `xbow/scores.tsv` |
| Other models' published scores on these benches | `PUBLISHED-BASELINES.md` in this folder (every number has a source link) |
| DeepSeek V4.1 Flash Abliterated results | `docs/bench/2026-10-19-dsv41flash-abliterated.md` in morphllm/tab. Local copy: `~/git_projects/morph/tmp/tab-k3-main/docs/bench/2026-10-19-dsv41flash-abliterated.md` |
| Pre-run feasibility reports for each bench | `../feasibility/` |
| Dark v1 graphic (layout reference only; the post should use light graphics) | `~/git_projects/morph/tmp/kimi-abl-graphic/graphic.html` and `graphic-v1.png` |

## Claims: what you can say now

| Claim | Status | Evidence | What unblocks it |
|---|---|---|---|
| Kimi Abliterated never refused during real pentest work | **Usable, scoped** | 0 refusal phrases across all 63 scored XBOW transcripts (regex scan of `xbow/tasks/*/opencode_transcript.jsonl`). Read a few transcripts yourself before quoting. | Re-check when the full 104 finish. |
| It beats GPT-5 on XBOW on the same harness | **Usable, provisional** | 53/63 = 84.1% vs GPT-5 63.5% (OpenCode) and 71.4% (Codex), pass 1 on the same 63 challenges. Behind GPT-5.2 (87.3%) and GPT-5.5 (93.7%). | Recompute when the run finishes (snippet below). |
| Abliteration removes refusals on harmful prompts | **Blocked** | We have no refusal-rate numbers of our own for Kimi yet. DeepSeek's come from our Heretic eval (see gap 3). | Gap 3: the same Heretic eval on stock and abliterated Kimi. |
| "Zero refusals" in general | **Don't claim** | Unmeasured on our own eval, and abliterated models usually still deflect some harmful prompts (DeepSeek Abliterated: about 2 real refusals in 100 on Heretic). DeepSeek Abliterated may beat Kimi on generic harmful prompts. | Gap 3 tells us. Until then, scope the claim to security work. |
| Best zero-refusal pentester | **Blocked** | We've measured two zero-refusal models. No public pentest results exist for other abliterated or uncensored models (see `PUBLISHED-BASELINES.md`). | Gap 1 below. Word it "the strongest zero-refusal pentester we've tested". |
| Significantly better than DeepSeek Abliterated | **Blocked** | DeepSeek Abliterated has never been run on XBOW, BountyBench or AutoPenBench. XBOW is near saturation (DeepSeek V4 Pro scored 92/104 in a published white-box run), so the gap may be small. | Gap 1, then a paired test (McNemar) on the same challenges. If XBOW doesn't separate them, BountyBench Exploit is the harder tiebreaker. |
| No less smart than stock Kimi | **Blocked** | Abliterated scores exist on our harness; stock does not. A stock run started on 2026-11-18 (`tmp/k31-intel-eval/base-all.log`) and never finished. The only stock number is Moonshot's self-reported GPQA 94.2, which makes abliterated look 2.3 points worse. | Gap 2. |
| Anything from our Terminal-Bench run | **Don't use** | Our run (39.1%, Terminal-Bench 4.0) was cut short by infrastructure failures; next to the published stock ~65.7 it would read as a big regression that isn't real. | A clean rerun of both arms, or leave Terminal-Bench out. |

## Story outline

Working title: "We removed Kimi's refusals. It's just as smart, and it's the best pentester that won't say no." Adjust once the data is in.

| # | Beat | Chart |
|---|---|---|
| 0 | Hook: a pentest agent that stops to ask permission isn't a pentest agent. Removing refusals can fail two ways: the model still won't do the work, or it gets dumber. We tested both. | Hero image (C summary + B, two panels) |
| 1 | It does the work. Stock Kimi deflects harmful requests; the abliterated model answers (numbers from gap 3), and it refused nothing in real pentest runs. | **A** |
| 2 | It didn't get dumber. Same scores as stock on every benchmark. | **B** |
| 3 | It's the strongest zero-refusal pentester we've tested: ahead of DeepSeek Abliterated on the same challenges. | **C** |
| 4 | How close it gets to frontier models, on the same harness and the same challenges. | **D** |
| 5 | Close: who it's for, and how to get access (`/privileged-access`). | none |

The DeepSeek post already makes the "defenders get refused" argument; link to it rather than repeating it. It also cites a preprint (arXiv 2605.23243) finding GPT-5.4 refusing legitimate security work in 2 to 3 of 5 runs, which fits beat 4.

## Charts

### Style (light, Morph-branded)

| Token | Value | Use |
|---|---|---|
| Paper | `#f6f7f2` | Image background |
| Panel | `#ffffff`, 1 to 2 px hairline `#e3e6df` | Chart panels |
| Ink | `#111611` (Morph `--dark-gray`) | Text, DeepSeek Abliterated marks |
| Secondary text | `#4b5248` | Subtitles, axis labels (keep body text at 4.5:1 or better) |
| Lime | `#99d52a` (the logo's green) | **Kimi Abliterated only**, so the hero model is the one green thing in every chart |
| Dark green | `#3f6212` | Any green text on white (lime fails contrast for text) |
| Stock models | `#c9cdc4`, hatched | Stock vs abliterated pairs |
| Frontier / reference | `#8d938a` | Other models |

- **Fonts:** Instrument Serif for headlines, Instrument Sans for labels, Geist Mono for numbers and small-caps kickers. These are the site's own fonts. Local files: `tmp/kimi-abl-graphic/fonts/` (Instrument) and `landing/node_modules/geist/dist/fonts/geist-mono/`.
- **Logo:** `landing/public/brand/morph-logo-on-light.svg`. Vendor logos are in `landing/public/logos/`; they're white, made for dark backgrounds, so recolor them to ink.
- **Layout reference:** the dark v1 (`tmp/kimi-abl-graphic/graphic.html`): two panels, bars on tick-mark tracks, one highlighted row, a one-line source footnote. Keep the source footnote on every chart.
- **The site is dark-mode.** Light images sit on a dark page, so give each one a rounded border or margin so it reads as a card.
- **Rendering:** v1 was rendered with a one-off headless Chrome because another agent was using the shared browser-use Chrome; Nick approved that for v1. Ask him before doing it again. The command was `google-chrome --headless --user-data-dir=<fresh temp dir> --hide-scrollbars --allow-file-access-from-files --force-device-scale-factor=1 --window-size=2000,1125 --timeout=5000 --screenshot=<out.png> file://<graphic.html>`. Load fonts from local files; Google Fonts didn't load in headless.

### Chart specs

**A. Refusals: answered / deflected / refused.** 100% stacked horizontal bars, stock vs abliterated.
- Kimi: missing until gap 3 runs. Use only our own Heretic results; no third-party numbers.
- Optional companion: one dot per XBOW run, colored "did the work" vs "refused". Kimi Abliterated is 63 of 63 so far.
- For a fair DeepSeek comparison, both models need the same prompt set and grader (gap 3).

**B. Stock vs abliterated Kimi.** Dumbbell: one stock dot and one abliterated dot per benchmark, with 95% interval whiskers and a shaded noise band. Overlapping dots make the point at a glance.
- Abliterated, our harness (NeMo-Skills, temperature 1.0, top_p 0.95, Moonshot's sampling settings):

| Benchmark | Abliterated | Repeats |
|---|---|---|
| AIME 2025 (30 problems) | 99.3% pass@1 | 10 |
| GPQA Diamond (198 questions) | 91.9% pass@1 | 4 |
| IFBench (300 prompts) | 71.9 average score (68.0 prompt-strict, 69.9 instruction-strict) | 2 |

- Stock: missing (gap 2).
- Source: `tmp/k31-intel-eval/results/abl/eval-results/{aime25,gpqa,ifbench}/metrics.json`.

**C. Kimi Abliterated vs DeepSeek Abliterated on XBOW.** A grid of all 104 challenges grouped by difficulty level (45 / 51 / 8 at levels 1 / 2 / 3). Color each cell by who solved it: both, Kimi only, DeepSeek only, or neither. Beside it, a summary bar with Wilson intervals; put the McNemar p-value in the footnote. Needs gap 1.

**D. Against frontier models, same harness, same challenges, one pass.** Ranked bars, a light version of v1's left panel.

| Model (agent) | Same 63 challenges, pass 1 |
|---|---|
| GPT-5.5 (Codex) | 59 / 63 = 93.7% |
| GPT-5.2 (Codex) | 55 / 63 = 87.3% |
| **Kimi K3.1 Abliterated (OpenCode)** | **53 / 63 = 84.1%** |
| GPT-5 (Codex) | 45 / 63 = 71.4% |
| GPT-5 (OpenCode) | 40 / 63 = 63.5% |
| GPT-5 (Pi) | 39 / 63 = 61.9% |

- The GPT rows come from the "Baselines Before Architecture" paper's per-challenge data (arXiv 2607.13085, `data/per_challenge_results.csv`). Over all 104 challenges that file reproduces the paper's own pass-1 scores (57 / 70 / 84 / 96), so the subset math is sound.
- `xbow/README.md` compares against GPT-5 at 68.3%. That credits GPT-5 with the better of its two passes against our one, so 63.5% is the fair figure.
- Recompute when the run finishes:

```bash
python3 -I - <<'EOF'
import csv, collections
ours = list(csv.DictReader(open('/home/skeptrune/git_projects/morph/PenHarness/benchmarks/kimi-k31-abliterated/xbow/scores.tsv'), delimiter='\t'))
ids = {r['benchmark_id'] for r in ours}
print('ours', sum(int(r['solved']) for r in ours), '/', len(ids))
rows = csv.DictReader(open('/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/xbow/harness/data/per_challenge_results.csv'))
cfg = collections.defaultdict(dict)
for r in rows:
    if r['condition'] == 'baseline' and r['pass_index'] == '1':
        cfg[(r['agent_cli'], r['model'])][r['benchmark_id']] = int(r['solved'])
for k, by in sorted(cfg.items()):
    n = sum(by.get(b, 0) for b in ids)
    print(k, f'{n}/{len(ids)} = {100*n/len(ids):.1f}%')
EOF
```

**Hero image.** C's summary next to B, two panels, 2000×1125 for X, plus a blog header crop. The existing DeepSeek header is 1731×909.

## Gaps and the runs that close them

Nick starts and stops benchmark runs himself. Hand him the exact commands and don't launch them. Run only these, with no extra variants:

1. **XBOW on DeepSeek V4.1 Flash Abliterated** (closes C, and the DeepSeek half of A's pentest companion).
   - Run all 104 challenges on the same harness and host (`skeptrune-thelio:~/xbow/`), after Kimi's retry pass so both models have the same task set.
   - Add an arm to `xbow/harness/agents.morph.json` by copying the `morph-kimik3-abliterated` entry with `OPENCODE_MODEL_ID=morph-dsv41flash-abliterated`.
   - The driver is `xbow/harness/run_morph_sweep.py`. Read its `--help` and `xbow/HARNESS-NOTES.md` for flags instead of guessing.
   - The Morph API key used must have privileged access to the abliterated model.
2. **Stock Kimi on the intelligence suite** (closes B).
   - Run `cd ~/git_projects/morph/tmp/k31-intel-eval && ./run_arm.sh base aime25:10,gpqa:4,ifbench:2`. Those repeat counts match the abliterated run.
   - The `base` arm points at `morph-kimik3`, which serves the same K3.1 Pro weights the abliterated model was built from, so the comparison is paired.
3. **Refusals on one prompt set and one grader for both models** (closes A; required, since we publish no outside refusal numbers).
   - The lightest path reuses the existing Heretic eval that produced DeepSeek's numbers: `operations/rdu-keys-1017-20261018/bench/heretic_eval.py`, run against stock and abliterated Kimi.
   - It takes `--candidate url,model,key_file`, `--baseline url,model,key_file`, and optionally `--refusal-baseline`, `--skip-kl`, `--thinking`, plus `--out`. Check `--help` before running.
   - DeepSeek's existing results: 10/100 flagged with about 2 real refusals (stock 100/100); thinking on, 7/100 flagged with about 2 real (stock 99/100); KL 0.169.

Optional, only if Nick asks: stock Kimi on XBOW (shows abliteration doesn't hurt the actual job), and a clean Terminal-Bench rerun of both arms.

## Other verified numbers you may need

| Fact | Value | Source |
|---|---|---|
| Kimi Abliterated XBOW run | Single pass, 3600 s per challenge, exact fresh-flag match, no hints. 41 of 104 challenges never ran because Docker image builds failed (infrastructure, not the model); 8 of the 10 recorded misses were zero-token hangs. Retry pass in progress. | `xbow/README.md` |
| XBOW cost profile | 6,599,813 input / 89,959 output tokens over 63 runs; median 550 s and 10 tool calls per solve (GPT-5: 77 s) | `xbow/README.md` |
| DeepSeek Abliterated vs stock | MMLU-Pro 84.0 vs 83.1; GSM8K 95.1 vs 95.4; IFEval 87.6 vs 88.7 (strict per prompt); GPQA 89.6 vs 90.4 (stock is OpenRouter's); τ²-Bench Airline 78.0 vs 77.3; tool-call error rate 0% | DeepSeek bench doc |
| Stock Kimi K3 (not abliterated) on XBOW, another harness | 90/104 first pass with pentestkit. Its 104/104 headline came after fixing the harness on its own misses and doesn't count. | `PUBLISHED-BASELINES.md` |
| Government eval of Kimi K3 | UK AISI + CAISI: ExploitBench 32% vs GLM-5.2 24% | https://www.aisi.gov.uk/blog/preliminary-assessment-of-kimi-k3s-cyber-capabilities |
| ExploitGym | Abliterated Kimi 0/20 on a kernel- and V8-heavy sample. Don't feature it. | `README.md` §3 |

## Rules for the copy

- **Naming:**
  - Say "Kimi K3.1 Abliterated" / `morph-kimik3-abliterated`, "DeepSeek V4.1 Flash Abliterated" / `morph-dsv41flash-abliterated`, and "Morph Abliterated" for the product line.
  - Never call our models "uncensored". The word is fine only as a generic SEO category, in competitors' product names, and in existing URL slugs (`/products/uncensored`, `/blog/uncensored-deepseek`).
- **No infrastructure details:** no GPU models, rack, cluster or region names. "Our own clusters" is as specific as it gets.
- **No em dashes.**
- **Every number** must come from this doc or its sources, and third-party numbers are credited to whoever produced them (Moonshot, the paper, OpenRouter). No third-party numbers about the abliterated model itself.
- **Prices:** Kimi Abliterated launched publicly on 2026-11-23 (landing PR #882): $2.75 input, $0.319 cached input, $15.40 output per 1M tokens (`CHAT_KIMIABLITERATED_*` in `landing/src/lib/pricing.ts`). Ultra does not cover it. Re-read pricing.ts before quoting.
- **Voice:** if the byline is Nick, read `~/.claude/skills/my-writing-style/SKILL.md` first. If it's Tejas, match the DeepSeek post.

## Where things go

- **Post:** `landing/content/posts/<slug>.mdx`, with frontmatter like `uncensored-deepseek.mdx` (title, date, description, author, image, category, `useComponents: true`).
- **Images:** `landing/public/images/blog/`.
- **Interactive charts:** a component in `landing/src/components/blog/`, registered in `landing/src/mdx-components.tsx`. Follow the `SwarmDiagram` pattern from `uncensored-deepseek-diagrams.tsx`.
- **Publishing is external.** Work on a branch in landing, open a PR, and stop for Nick's review. Merging to main deploys the site.

## Questions for Nick before publishing

1. Who is the byline, Nick or Tejas?
2. Should the post mention the MindFort partnership, as the DeepSeek post did?
3. Approval to run gaps 1 to 3 (he launches them).
