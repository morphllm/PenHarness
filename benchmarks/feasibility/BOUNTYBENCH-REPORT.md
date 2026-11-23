# BountyBench runnability check

Date: 2026-11-22. Read-only review. Nothing from the clone was executed, installed or built. No requests were sent to the Morph endpoint or any model API.

## Verdict

**It can run, but only after a small patch.** It does not run as-is.

- **The provider patch.** The harness has no `base_url` setting, its OpenAI path uses the Responses API, and its key check is hardcoded to `api.openai.com`. The fix copies the existing xAI provider and touches about 40 to 60 lines in 5 files.
- **The CPU architecture.** The published agent image is arm64-only.
  - **arm64 cloud VM (GCP C4A or AWS Graviton):** uses the published image unchanged and matches the paper's hardware (GKE C4A). This is the fastest way to get numbers we can compare to the paper.
  - **x86_64 Linux workstation (1.8 TB disk, Docker):** also works. It needs a local amd64 build of the agent image and a 1-line change that stops the runtime image pull. Disk and RAM are more than enough.

## 1. Official repo

- **Repo:** https://github.com/bountybench/bountybench
- **Why it's the official one:** the NeurIPS 2025 paper says "all of our code is available on https://github.com/bountybench/bountybench ... Both of these are linked to at https://bountybench.github.io/". The text is in the NeurIPS PDF; extracted copy at `paperpdf/p.txt`.
  - The arXiv footnote points to bountybench.github.io, which returned 404 on 2026-11-22.
- **Ignore:** `EnvCommons/BountyBench` is a third-party wrapper.
- **Clone:** `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/bountybench/` (the directory was confirmed empty before cloning).
  - HEAD is `8ece6aab`, dated 2025-06-21, "Add license (#1062)".
  - The `bountytasks` submodule is at its pinned commit `60311f3d` (2025-05-15). The nested target codebases were not fetched.
- **Paper:** PDF and extracted text are in `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/paperpdf/`.

## 2. License

- **Main repo:** Apache-2.0 (`LICENSE:1-3`). It was only added in the last commit. Apache has no extra use restrictions.
- **Tasks:** `bountytasks/` has no LICENSE file at the pinned commit.
- **Target codebases:** forks under the `cy-suite` GitHub org that keep their upstream licenses. All 31 repos are public; each one answered `git ls-remote`.

## 3. Model and provider config

**Flags** (`workflows/runner.py:83-97`): `--model`, `--max_input_tokens`, `--max_output_tokens`, `--phase_iterations`, `--use_helm`, `--use_mock_model`.

**How the provider is picked:** from the model-name prefix in `get_model_provider` (`resources/model_resource/model_resource.py:124-173`).
- Supported prefixes: `anthropic/`, `google/`, `meta-llama|mistralai|deepseek-ai/` (Together), `openai/`, `xai/`.
- Any other prefix raises "Unknown model type".

**max_tokens is passed and can be set.**
- It is passed as `max_tokens=self.max_output_tokens` (`model_resource.py:262-269`) and set with `--max_output_tokens`.
- Code defaults are 8192 input / 4096 output (`workflows/exploit_workflow.py:25-26`; the other workflow files match).
- The paper used 8192 / 8192.
- Input is cut to `max_input_tokens` with tiktoken, keeping the start and end and cutting the middle (`model_utils.py:98-119`).
- Temperature is fixed at 0.5 with no CLI flag (`model_resource.py:41`).
- Each call times out after 300 s (`model_resource.py:44`).
- Raising the output cap for a reasoning model makes our numbers less comparable to the paper's.

**Why `OPENAI_BASE_URL` with an `openai/<id>` model doesn't work:**
- The key check calls `https://api.openai.com/v1/models` and requires our model id to be in OpenAI's list (`services/auth_helpers.py:230-262`). It would fail, and it would send our Morph key to OpenAI.
- `OpenAIModels` uses `client.responses.create` (the Responses API), not chat completions (`openai_models/openai_models.py:51-65`).
- The model must be listed in `NonHelmMapping`, or the lookup raises (`model_mapping.py:333-350`).

**Smallest patch.** The closest existing pattern is `xai_models/xai_models.py`, which already does chat completions with a `base_url`, `max_tokens` and prefix stripping.
1. Add `resources/model_resource/morph_models/morph_models.py`, copied from `xai_models/xai_models.py:16-142`.
   - Read `base_url` from an env var such as `MORPH_BASE_URL`.
   - Turn a None `content` into an empty string. Otherwise a reply that runs out of tokens mid-reasoning crashes in `remove_hallucinations`.
   - The key env var resolves automatically to `MORPH_API_KEY` from the class name (`model_provider.py:156`).
2. Add an `elif model_prefix == "morph"` branch in `get_model_provider` (`model_resource.py:136-172`).
3. Add `ServiceProvider.MORPH` and a `PROVIDER_CONFIG` entry with `api_key_name="MORPH_API_KEY"` in `services/service_providers.py:15-62`.
4. Add `_auth_morph_api_key` in `services/auth_helpers.py` that returns `(True, "")`, so the run makes no extra `/models` request.
5. Add `morph/morph-kimik3-abliterated` and `morph/morph-kimik3` to `NonHelmMapping` in `model_mapping.py`.
6. Set `OPENAI_API_KEY` to a dummy value. Input truncation always creates `OpenAIModels()` (`model_utils.py:21-32`, `model_provider.py:146-178`); the tokenizing itself runs locally with tiktoken.
7. Create a `.env` file, or validation fails (`api_key_service.py:72-77`). If a key is missing, the run stops and waits for typed input (`api_key_service.py:81-85`), which hangs an unattended run.
8. `run_experiments.py:91-125` doesn't pass `--max_output_tokens` or `--max_input_tokens` through. Add 2 lines there, or call `python -m workflows.runner` directly.

**Notes:**
- `<think>` blocks are stripped from replies, and a reply with an unclosed `<think>` block becomes an empty string (`model_resource.py:187-199`).
- If our endpoint returns reasoning in a separate field, `content` is the final answer only, which works fine.

## 4. Agent harness

- **Bundled:** the "C-Agent", built on the Cybench agent (`agents/executor_agent/` plus the detect, exploit and patch agents). Our model would run under it.
- **What the paper used:** 10 agents in total.
  - Claude Code with Claude 3.7.
  - Codex CLI with o3-high and with o4-mini.
  - C-Agents with o3-high, GPT-4.1, Gemini 2.5 Pro Preview, Claude 3.7 Sonnet Thinking, Qwen3 235B A22B, Llama 4 Maverick and DeepSeek-R1.
- **Claude Code and Codex CLI** exist only on unmerged branches: `origin/claude-code` (2025-05-17), `origin/codex-agent` (2025-05-09), `origin/claude_agent` and `origin/codex`. They are tied to those vendors' own models, so compare us only against the C-Agent rows.
- **C-Agent settings in the paper:** at most 50 model calls, 8192 input / 8192 output tokens, and a 1024-token thinking budget for Claude 3.7.

## 5. Infra needs

**Docker on the host.**
- Target apps start on the host with docker compose (each system's `setup_repo_env.sh`), on a normal bridge network `shared_net` (`workflows/utils.py:9-14`). It isn't marked internal, so containers can reach the internet.
- Verify scripts run on the host (`agents/exploit_agent/exploit_agent.py:231-238`).

**Privileged agent container (docker-in-docker).**
- `cybench/bountyagent:latest` runs with `privileged=True, cgroupns="host"` (`resources/kali_env_resource.py:385-393`).
- It starts its own Docker daemon inside (`tools/entrypoint.sh:3`).

**CPU architecture.**
- Docker Hub has only an arm64 `latest` image (pushed 2025-05-05, about 5.2 GB compressed). I checked the Docker Hub API.
- Issue #1065 (open) reports "exec format error" on x86.
- The repo's x86 CI uses qemu emulation (`.github/workflows/ci.yml:126,138`).
- **x86 fix:**
  - Build with `tools/build.sh --local --arch amd64`.
  - Remove or guard the unconditional `images.pull` (`kali_env_resource.py:373-380`); otherwise it overwrites the local image with the arm64 one, which is what #1065 shows.
  - The image name is also hardcoded in `exploit_agent.py:287`, `patch_agent.py:324`, `detect_agent.py:421` and `detect_patch_agent.py:439`.
- The base image `cybench/kali-linux-large:latest` publishes both amd64 and arm64.
- The target apps handle both architectures (for example `bountytasks/LibreChat/Dockerfile:26-29`).

**Resources.**
- The paper gave each task 1 vCPU, 6 GiB RAM (2 to 3 GiB actually used) and 30 to 40 GB of SSD.
- `bountytasks` alone is 364 MB before the nested codebases.
- My estimate is 150 to 300 GB in total for images and codebases. That fits on 1.8 TB.

**Host needs.** Python 3.11 (`setup.sh:28-34`) and `tree`.

**External pulls.** Docker Hub (the agent image is pulled on every container start), PyPI and npm during target builds, and 31 `cy-suite` GitHub repos.

**Cloud.** No cloud service is needed. `k8s/` is the GKE setup the paper used and is optional; HELM (Stanford's model API) is optional too.

**Don't run `setup.sh`.** Its `git submodule update --remote` (`setup.sh:77-79`) moves the tasks off the pinned commit. Pin the submodule instead.

## 6. Tasks, scoring and subsets

**Count.**
- The paper has 40 bounties across 25 systems, times 3 modes: 40 tasks per mode, 120 in total.
- Detect was also run with extra hints: CWE, and CWE plus title.
- At the pinned commit the repo has 46 bounties across 31 systems.
- The 6 not in the paper: imaginairy 0, llama_index 0, neural-compressor 0, node 0, open-webui 0, paddle 0.

**Scoring.**
- **Exploit:** passes if the exploit plus verify script exits 0 on the vulnerable code and 1 on the patched code (`exploit_agent.py:183-224`).
- **Patch:** passes if the repo and bounty unit tests still meet their thresholds and the exploit then fails (`patch_agent.py:216-248, 433-470`). The paper ran the tests 3 times and kept the best score.
- **Detect:** passes if the agent's exploit fails on a patched bounty and succeeds on that bounty's unpatched version (`detect_agent.py:215-282`).
- **Attempts:** up to 3 per task, one submission each, counted as a pass if any succeeds. Dollar values come from `bounty_metadata.json`.
- **Iterations:** the code defaults to 10 (`phases/base_phase.py:28`). Use `--phase_iterations` to match the paper's 50 model calls.

**Subsets: yes.**
- Each run is one task directory, bounty number and workflow (`workflows/runner.py:66-97`).
- `run_experiments.py:42-85` takes a YAML of tasks × models. On Linux it opens each run in a terminal window (`run_experiments.py:180-219`), so on an unattended box loop over the runner instead.

## 7. Wall time and token volume

**Time per task (paper Appendix O).** This counts model time but not container setup or teardown.

| Agent | Average time per task |
|---|---|
| C-Agent GPT-4.1 | 12.31 min |
| C-Agent Llama 4 | 17.38 min |
| C-Agent o3-high | 22.24 min |
| C-Agent Qwen3 | 22.77 min |
| C-Agent Gemini 2.5 | 23.21 min |
| C-Agent Claude 3.7 | 28.20 min |
| C-Agent DeepSeek-R1 | 37.27 min |
| Claude Code | 7.83 min |
| Codex CLI o4-mini | 7.93 min |
| Codex CLI o3-high | 13.30 min |

My estimate: 50 to 150 hours per model if run one after another (120 tasks, about 25 minutes each, up to 3 attempts). Two models is double that, so we'd need parallel lanes.

**Tokens per C-Agent per mode, last attempt only (paper Appendix R):**

| Mode | Input tokens | Output tokens |
|---|---|---|
| Detect | 2.1 to 10.2M (Table 37) | 0.8 to 3.4M (Table 47) |
| Exploit | 0.7 to 5.1M (Table 38) | 0.15 to 1.7M (Table 48) |
| Patch | 1.7 to 7.7M (Table 39) | 0.65 to 2.2M (Table 49) |

- Reasoning models (o3-high, DeepSeek-R1) are at the high end for output.
- Retries can multiply these by up to about 3×.
- Each run makes one model call at a time, so load on our endpoint equals the number of parallel lanes. This matters given the concern about degrading production.

## 8. Published scores

From Table 1 of the paper, up to 3 attempts. Sources: https://arxiv.org/html/2505.15216 and https://papers.nips.cc/paper_files/paper/2025/file/faed4276b52ef762879db4142655c699-Paper-Datasets_and_Benchmarks_Track.pdf

| Agent | Detect % | Detect $ | Exploit % | Patch % | Patch $ |
|---|---|---|---|---|---|
| C-Agent: Claude 3.7 Thinking | 5.0 | $1,025 | 67.5 | 60.0 | $11,285 |
| C-Agent: GPT-4.1 | 0.0 | $0 | 55.0 | 50.0 | $4,420 |
| C-Agent: Gemini 2.5 | 2.5 | $1,080 | 40.0 | 45.0 | $3,832 |
| C-Agent: o3-high | 0.0 | $0 | 37.5 | 35.0 | $3,216 |
| C-Agent: DeepSeek-R1 | 2.5 | $125 | 37.5 | 50.0 | $4,318 |
| C-Agent: Llama 4 Maverick | 0.0 | $0 | 42.5 | 42.5 | $10,425 |
| C-Agent: Qwen3 235B A22B | 0.0 | $0 | 17.5 | 25.0 | $1,344 |
| Claude Code (reference only) | 5.0 | $1,350 | 57.5 | 87.5 | $13,862 |
| Codex CLI: o3-high (reference only) | 12.5 | $3,720 | 47.5 | 90.0 | $14,152 |
| Codex CLI: o4-mini (reference only) | 5.0 | $2,400 | 32.5 | 90.0 | $14,422 |

- The C-Agent rows are the ones to compare against.
- The open-weight models were served through Together.
- I found no results for newer models. https://benchmarklist.com/benchmarks/bountybench/ only repeats the paper's numbers. Stanford CRFM's blog post: https://crfm.stanford.edu/2025/05/21/bountybench.html

## 9. Known breakage

Open GitHub issues:

| Issue | Problem |
|---|---|
| bountybench #1065 (2025-09-26) | x86 unsupported: the image is arm64-only, giving "exec format error". No reply. |
| bountybench #1063 | `setup.sh` only checks the default `python`, so it fails when that isn't 3.11 |
| bountybench #932 | after a failed iteration the run switches to interactive mode and hangs; the code path is still at HEAD (`phases/base_phase.py:332-334`) |
| bountybench #873 | DeepSeek-R1 through Stanford's HELM API |
| bountybench #818 | possible to overload the model with requests |
| bountybench #850 | ExploitWorkflow fails during codebase cleanup |
| bountytasks #721 | pytorch-lightning bounty 0 needs a fix |
| bountytasks #416 | undici's tests need a fix |
| bountytasks #574 | executable permission bits set inconsistently on files |

Maintenance is low. The last code commit is from 2025-06-21, and the Docker Hub image (2025-05-05) is older than the repo HEAD.

## 10. Where to run it

- **arm64 cloud VM:** the published image works unchanged and the hardware matches the paper. Needs only the provider patch.
- **x86_64 workstation:** needs the provider patch, the local amd64 image build and the change that stops the image pull.
- **Concurrency on one host:** not verified. Separate runs of the same task may clash over fixed container names. The paper ran each task in its own Kubernetes pod.
