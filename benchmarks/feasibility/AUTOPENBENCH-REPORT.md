# AutoPenBench feasibility check

## Verdict

**Runnable after a small patch, in autonomous mode only.**

- The assisted mode stops and waits for a human to type each sub-task, so it can't run unattended.
- The benchmark itself is fine: MIT license, 33 Docker tasks, and the flag check is a plain string match.
- Two things block us. The paper's agent only talks to OpenAI or Gemini models. And the benchmark repo's latest commit breaks that agent.

To get a number for `morph-kimik3-abliterated` and `morph-kimik3`:

1. Pin auto-pen-bench to commit `7f8a634`.
2. Add about 15 lines to the paper's model client (`genai/llm/llm.py`) so it accepts any OpenAI-compatible endpoint, with `max_tokens` passed through.
3. Read the model name from an env var in the experiment scripts, and fix two loop-count bugs in them.
4. Add a `docker-compose` shim.

**Box:** a throwaway x86_64 cloud VM, not this workstation. Section 5 explains why.

**What I touched:**

- No requests went to the Morph endpoint or to any model API.
- Nothing from either repo was run, installed or built.
- The only network calls were two git clones, read-only GitHub API calls, the arXiv and ACL Anthology pages, and one Docker Hub tag lookup.

**Clones** (both read-only):

- `APB` = `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/autopenbench`, the benchmark. HEAD `9c4890a`, 2025-10-28. Paths below are relative to this folder unless marked "paper:".
- `paper:` = `/home/skeptrune/git_projects/morph/tmp/pentest-bench-check/genai-pentest-paper`, the paper's agent code. HEAD `40e01e5`, 2025-10-28. It's a sibling folder because the agents live there, not in the benchmark repo.

## 1. Official repo

- The arXiv abstract and comments for [2410.03225](https://arxiv.org/abs/2410.03225) link two repos:
  - `github.com/lucagioacchini/auto-pen-bench` for the benchmark.
  - `github.com/lucagioacchini/genai-pentest-paper` for the experiments.
- APB `README.md:2-17` cites the same paper.
- auto-pen-bench has 112 stars and 26 forks. Its last push was 2025-10-28.
- The paper was also published at EMNLP 2025 Industry Track (2025.emnlp-industry.114).

## 2. License

- MIT, copyright 2024 Luca Gioacchini (APB `LICENSE:1-3`). The paper repo is also MIT (paper: `LICENSE`).
- Neither license restricts how the code may be used.
- The only restriction-like text is a "research purposes only" line inside the o1 prompt path (paper: `genai/llm/llm.py:58-59`). It's prompt text, not a license term.

## 3. Model and provider configuration

**Benchmark repo:**

- It has no agent and no model setting. It provides only the tools and the driver.
- The judge model is hard-wired. `Evaluator` builds `OpenAI(api_key=...)` with the default base URL and model `'gpt-4o'` (APB `autopenbench/evaluation/evaluator.py:52-54`). It only affects the progress score.
- The README example notebook also hardcodes `gpt-4o` (APB `examples/instructor_agent.ipynb`, cells 6, 14 and 20).

**Paper agent:** you pick the provider with a substring of the model name (paper: `genai/llm/llm.py:83-105`).

- `'gpt'` uses instructor on `OpenAI(api_key)` with `temperature=0, seed=0`.
- `'o1'` sends free text to the model, then converts the answer with a gpt-4o adapter.
- `'gemini'` uses Google's client.

What's missing:

- There's no `base_url` parameter, and `max_tokens` is never passed. A grep of `genai/` finds no `max_tokens`.
- A name like `morph-kimik3-abliterated` matches no branch. No client is created, and `invoke()` fails at `return llm_out` (`llm.py:197`).
- The model name and OpenAI key are hardcoded in each script, e.g. paper: `experiments/01-autonomous_agent/access_control.py:15,35-38`.

**Smallest patch:**

- **`LLMClient.__init__` and `LLMClient.invoke`** (paper: `genai/llm/llm.py`):
  - Add an `else` branch: `instructor.from_openai(OpenAI(base_url=AGENT_BASE_URL, api_key=AGENT_KEY), mode=instructor.Mode.JSON)`.
  - In `invoke`, pass `max_tokens=N` and `max_retries=5` to `chat.completions.create`. N is free to set; use 8k or more for a reasoning model.
  - Don't set `OPENAI_BASE_URL` globally. The OpenAI SDK reads that variable, so it would also redirect the gpt-4o judge.
- **Why JSON mode:** instructor's default mode forces tool calls. The paper says gpt-4o-mini and DeepSeek failed on "structured output format". I haven't checked how our endpoint handles a forced `tool_choice`.
- **Experiment scripts:** read the model id from an env var instead of the hardcoded one.

## 4. Agent harness

**The benchmark doesn't include an agent.**

- It only provides tools: `ExecuteBash`, `SSHConnect`, `WriteFile`, `FinalAnswer` (APB `autopenbench/tools/*`).
- Its README example is a single-step notebook (APB `examples/instructor_agent.ipynb`).

**The paper's agents** live in genai-pentest-paper and run on AgentQuest v1 (paper: `setup.sh:5-13`). The paper used `gpt-4o-2024-08-06` at temperature 0 for both agents.

**Autonomous agent:**

- Each step makes three LLM calls: summary, thought, action (paper: `genai/agent/autonomous_agent.py:70-86`).
- The step limit is 30 for in-vitro tasks and 60 for real-world tasks (paper: `experiments/01-autonomous_agent/*.py:17`).
- No human is involved, so it runs unattended.

**Assisted agent:**

- It blocks on `input('Provide your task:\n')` (paper: `genai/agent/assisted_agent.py:67`).
- It asks again whenever it decides a sub-task is done, or after 7 steps on one sub-task (`assisted_agent.py:57,142`; paper: `experiments/02-assisted_agent/access_control.py:73-75`).
- The paper says this mode assumes "the human pentester knows the vulnerabilities".
- It can't run unattended. Scripting the sub-tasks would make the results incomparable with the paper's.

**HEAD breaks the paper's pipeline:**

- PR #6 (`9c4890a`, 2025-10-28) removed `PentestDriver.step()`, which held the flag check.
- AgentQuest's driver still calls `self.environment.step(action)` (agentquest v1 `agentquest/drivers/autopenbench/autopenbench_driver.py:26`).
- AgentQuest's setup clones auto-pen-bench HEAD without a pin (agentquest `benchmarks/autopenbench/setup.sh:2`).
- Commit `7f8a634` (2025-10-16) still has `step()` (`pentest_driver.py:184`, flag check at 224-228). Pin to that commit.

## 5. Infra

**Docker:**

- On every task, the driver runs `docker-compose` down and up by calling the literal v1 command (APB `autopenbench/driver/pentest_driver.py:48-101`).
- This box has only the Compose v2 plugin (Docker 29.1.3, Compose v5.5.1), so it needs a `docker-compose` shim.

**Network:**

- One bridge network is hardcoded as `192.168.0.0/16` in every compose file (e.g. APB `benchmark/machines/docker-compose.yml:20-24`).
- The Kali container is fixed at `192.168.0.5`. The host-side driver connects to it over SSH as root/root (`pentest_driver.py:150-157`).

**Privileges:**

- No container is privileged.
- The network-security containers get `cap_add: NET_ADMIN` (APB `benchmark/machines/in-vitro/network_security/docker-compose.yml:11` and the others in that file).
- One database gets `SYS_NICE` (`web_security/docker-compose.yml:109`).
- Host port 8080 is published by web_security vm4 and cve vm2 (`web_security/docker-compose.yml:94`, `real-world/cve/docker-compose.yml:36-37`). Those are deliberately vulnerable services on the host's network.

**Architecture:**

- x86_64 only.
- The Kali base image `lucagioacchini/kali-linux-headless:latest` is amd64-only, 2.1 GB compressed, last updated 2024-04-17 (Docker Hub tag API).
- Several target bases are old, e.g. `openjdk:8u102-jre` and `ubuntu:16.04` (APB `benchmark/machines/real-world/cve/vm7/Dockerfile:1`, `vm9/Dockerfile:1`).

**Building needs the internet; running doesn't:**

- Base images from Docker Hub: Debian, PHP, MySQL, Jenkins, Grafana, Tomcat, Maven, OpenJDK.
- Build-time downloads from SourceForge (GeoServer, pcre, zlib), archive.apache.org (Druid, Solr), download.samba.org, nginx.org and GitHub (OpenSSL tarball, Bludit 3.9.2).
- The Kali build uses a hardcoded Italian mirror, then runs `apt full-upgrade` on Kali rolling (APB `kali/Dockerfile:13-21`). Builds aren't reproducible.

**One task at a time:**

- Each task tears down every stack, then starts Kali plus the target (`pentest_driver.py:47-74`).
- Container names and IPs are fixed, so tasks can't run in parallel on one Docker host.
- Comparing the base and abliterated models side by side needs two VMs. On one VM the passes run one after the other.

**Disk and RAM:**

- Neither repo states them.
- My estimate is 40-80 GB of images and under 16 GB RAM. Only one target runs at a time, and the largest are single JVM services.

**Cloud dependency:** none, except OpenAI for the gpt-4o judge.

### Why not this workstation

- Docker stores its data in `/var/lib/docker` on the root disk (`/dev/sda3`, 457 GB). That disk has 14 GB free, 97% full.
- The 1.9 TB NVMe is mounted at `/mnt/skeptrune-backup` (386 GB free). It isn't Docker's storage.
- `wlp34s0` is at `192.168.1.119/24`. That collides with the benchmark's hardcoded /16, and with the access-control targets at `192.168.1.0-4`.
- Publishing port 8080 would put a Spring4Shell target and a SQL-injection web app on the LAN and on Tailscale.

### Recommendation

- A throwaway x86_64 cloud VM with about 8-16 vCPU, 32 GB RAM and 150-200 GB disk.
- Use one VM per model if both should run at the same time.

## 6. Tasks and scoring

**33 tasks** (APB `data/games.json`):

| Level | Category | Tasks |
|---|---|---|
| in-vitro | access_control | 5 |
| in-vitro | web_security | 7 |
| in-vitro | network_security | 6 |
| in-vitro | cryptography | 4 |
| real-world | cve | 11 |

The CVE tasks (APB `README.md:124-171`) are GeoServer, Jenkins, Spring4Shell, Baron Samedit, Apache path traversal, Grafana, Druid, Log4j, Bludit, SambaCry and Heartbleed.

**Success rate (SR)** is a deterministic flag match.

- At `7f8a634`, the submitted flag is cut to 16 characters and checked with `self.flag in tool.flag` (`pentest_driver.py:224-228`). A match returns "You Won!" and `done=True`.
- SR doesn't need the judge.

**Progress rate (PR)** is the share of command milestones the agent reached.

- On every step, the gpt-4o judge is asked once about each milestone not yet reached (APB `autopenbench/evaluation/evaluator.py:92-97`).
- Stage milestones are worked out from those counts (`evaluator.py:101-109`).
- The paper also let a human correct judge mistakes (arXiv Section 2.3).
- We can report SR only and skip the judge. Or we keep gpt-4o as the judge so PR stays comparable with the paper.

**Subsets are easy:**

- Each experiment script loops over one category.
- Any single task loads with `load_data(level)[category][i]` (APB `autopenbench/utils/utils.py:11-22`). Its milestones load with `load_milestones` (`utils.py:25-41`).

## 7. Wall time and tokens

Neither repo nor the paper states these. The paper only says network-security scanning took "approximately one hour". My estimates below come from the paper's own gpt-4o autonomous logs (paper: `logs/experiments/auto_*.json`).

- **Steps:** 1,165 agent steps across the 33 tasks.
  - The maximum budget is 1,320 steps (22×30 + 11×60).
  - Weaker models fail more and use more of the budget.
- **Context grows with every step:**
  - The summary call resends the full history each time (paper: `genai/working_memory/scratchpad.py:48-57`, called with `last=None` from `autonomous_agent.py:65`).
  - The history is never truncated.
- **Input volume:** summed over every step, about 106M characters. That's roughly 26M input tokens for the summary calls alone.
- **Per model, per full autonomous pass:**
  - About 30-40M input tokens.
  - A few million reasoning output tokens, across about 3.5k agent calls.
  - Separately, about 5-9k gpt-4o judge calls.
- **Context length:** the longest CVE run (cve vm3) reaches about 509k characters of history, roughly 127k tokens. The model needs well over 128k context.
- **Wall time:** about 1-2 days per model, one task at a time. This assumes 1-2 minutes per step with a reasoning model. Each step is three LLM calls plus shell reads with a 5 s timeout and up to 3 retries (APB `autopenbench/shell/remote_shell.py:58,159`).
- **Production impact:** two models is about 60-80M tokens of real load on the endpoint. Use a non-prod replica or run off-peak.

## 8. Published scores

There is no leaderboard.

**Main results:**

- Source: [EMNLP 2025 Industry paper](https://aclanthology.org/2025.emnlp-industry.114/), Table 2. It matches [arXiv v2](https://arxiv.org/html/2410.03225v2), Table 4.
- Model: `gpt-4o-2024-08-06` at temperature 0.
- Runs: apparently one per task.
- PR is averaged over failed tasks only.

| Category (tasks) | Autonomous SR | Autonomous PR | Assisted SR | Assisted PR |
|---|---|---|---|---|
| Network security (6) | 0.50 | 0.08 | 0.67 | 0.25 |
| Access control (5) | 0.20 | 0.49 | 0.80 | 0.44 |
| Web security (7) | 0.29 | 0.40 | 0.57 | 0.42 |
| Cryptography (4) | 0.00 | 0.55 | 0.25 | 0.56 |
| In-vitro (22) | 0.27 | 0.40 | 0.59 | 0.43 |
| Real-world (11) | 0.09 | 0.39 | 0.73 | 0.76 |
| **Total (33)** | **0.21** | **0.39** | **0.64** | **0.53** |

**Model comparison:**

- Source: [EMNLP](https://aclanthology.org/2025.emnlp-industry.114/), Table 3.
- These ran on the easiest task only: AC0, a weak sudo password.
- Autonomous agent, 5 runs per model.

| Model | SR | PR (failed runs) |
|---|---|---|
| gpt-4o | 1.00 | – |
| deepseek-v3 | 0.60 | 0.250 |
| gpt-4-turbo | 0.40 | 0.120 |
| deepseek-r1 | 0.40 | 0.167 |
| gpt-4o-mini | 0.00 | 0.550 |
| o1-mini | 0.00 | 0.275 |
| o1-preview | 0.00 | 0.125 |
| gemini-1.5-flash | 0.00 | 0.050 |

Notes on these tables:

- The table labels gpt-4o-mini as "gpt-4-mini".
- The paper blames the gpt-4o-mini and DeepSeek failures on "structured output format". That's relevant for our reasoning model.
- Consistency check (EMNLP Figure 5): 10 runs of AC0 all succeeded. On AC2, the agent detected the vulnerability in only 30% of runs and exploited it in 40%.

## 9. Known breakage

1. **`PentestDriver.step()` is missing at HEAD.** PR #6 removed it on 2025-10-28. AgentQuest's driver and both README notebooks fail with AttributeError. Fix: pin `7f8a634`.
2. **Open PR #7 (2025-11-17) tries to restore `step()` but is wrong.**
   - It sets `done=True` after any successful action.
   - Its `FinalAnswer.run` returns None on a wrong flag.
   - Don't use it.
3. **The MCP path is broken.** Skip it entirely.
   - `autopenbench/mcp_server/__init__.py:1` imports `initializepentest_driver`, which doesn't exist, so importing the package raises ImportError.
   - The `mcp` package isn't in `setup.py:7-18`.
   - The server's `final_answer` doesn't check the flag (`mcp_server.py:88`).
   - The paper's MCP client hardcodes a developer's absolute path (paper: `experiments/05-mcp/client.py:113`).
4. **Issue #9 (open, 2026-09-09): the `openjdk` images are deprecated.**
   - It names cve vm6 (`openjdk:8-jre`). vm0 (`openjdk:17.0.2-slim`) and vm7 (`openjdk:8u102-jre`) use them too.
   - I didn't check whether they still pull.
   - The suggested fix is `eclipse-temurin`.
5. **Issue #8 (open, 2026-09-09) asks "Is this repo still maintained?"** It has no reply. The last push was 2025-10-28.
6. **Loop-count bugs in the paper's scripts:**
   - `experiments/01-autonomous_agent/crypto.py:20` uses `range(6)`, but there are only 4 cryptography tasks. It crashes with IndexError after task 4.
   - `cve.py:20` (in both the autonomous and assisted folders) uses `range(10)`, which skips vm10 (Heartbleed). The paper's logs do include vm10.
7. **The driver calls `docker-compose` v1** (`pentest_driver.py:48-101`). The repo's `setup/setup.sh:16-20` would run `sudo apt install docker-compose`. Use a v2 shim instead.
8. **Builds are fragile.** Kali rolling plus `apt full-upgrade`, a keyring `.deb` pinned to 2025.1, a hardcoded mirror (`kali/Dockerfile:13-21`), and live downloads from SourceForge and Apache.
9. **Cosmetic:** the network_security vm5 flag mount uses a wrong relative path (`in-vitro/network_security/docker-compose.yml:119`). It's harmless because the flag is hardcoded in `vm5a/duster.sh:3`.
10. **Irrelevant to us:** the Gemini branch in the paper's client references an undefined `prompt` (paper: `genai/llm/llm.py:187`).

## Sources

- [arXiv 2410.03225](https://arxiv.org/abs/2410.03225)
- [arXiv HTML v2](https://arxiv.org/html/2410.03225v2)
- [EMNLP 2025 Industry paper](https://aclanthology.org/2025.emnlp-industry.114/)
- [auto-pen-bench](https://github.com/lucagioacchini/auto-pen-bench)
- [genai-pentest-paper](https://github.com/lucagioacchini/genai-pentest-paper)
- [AgentQuest](https://github.com/nec-research/agentquest)
- [openjdk deprecation notice](https://hub.docker.com/_/openjdk)
