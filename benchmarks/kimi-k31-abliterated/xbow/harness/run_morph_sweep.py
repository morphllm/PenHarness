#!/usr/bin/env python3
"""Parallel XBOW sweep driver for the Morph Kimi K3 ablation study.

The harness runs one benchmark at a time (one compose project per task), which is
far too slow for 104 challenges. Each `run-xbow` invocation is independent -- the
harness plants its own flag, picks its own free port and names its compose stack
per benchmark -- so this driver just runs N of them concurrently and resumes.

Usage:
    export MORPH_API_KEY=...
    python scripts/run_morph_sweep.py --workers 6 --output-dir results-morph

Resume is automatic: a (benchmark, agent) pair whose result.json already scores is
skipped, so re-running the command continues an interrupted sweep.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = {"base": "morph-kimik3", "abliterated": "morph-kimik3-abliterated"}
LOCK = threading.Lock()


def log(message: str) -> None:
    with LOCK:
        print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def already_scored(output_dir: Path, benchmark_id: str, agent: str) -> bool:
    """True if any prior experiment recorded a scored result for this pair."""
    for result_path in output_dir.glob(f"experiments/*_{benchmark_id}_{agent}*/runs/**/result.json"):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("score") or {}).get("scored"):
            return True
    return False


def run_one(
    benchmark_id: str,
    agent: str,
    xbow_root: Path,
    config: Path,
    output_dir: Path,
    passes: int,
    timeout_seconds: int,
) -> tuple[str, str, int, str]:
    command = [
        sys.executable, "-m", "xbow_harness", "run-xbow",
        "--agent-config", str(config),
        "--agent", agent,
        "--xbow-root", str(xbow_root),
        "--benchmark-id", benchmark_id,
        "--passes", str(passes),
        "--output-dir", str(output_dir),
    ]
    env = os.environ.copy()
    started = time.time()
    try:
        completed = subprocess.run(
            command, cwd=str(HARNESS_ROOT), env=env, capture_output=True,
            text=True, timeout=timeout_seconds, check=False,
        )
        detail = (completed.stderr or "").strip().splitlines()[-1:] or [""]
        return benchmark_id, agent, completed.returncode, detail[0][:300]
    except subprocess.TimeoutExpired:
        return benchmark_id, agent, 124, f"driver timeout after {timeout_seconds}s"
    finally:
        log(f"done {benchmark_id} {agent} in {time.time() - started:.0f}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xbow-root", default=str(HARNESS_ROOT.parent / "repo-at-581eb1e"))
    parser.add_argument("--agent-config", default=str(HARNESS_ROOT / "configs" / "agents.morph.json"))
    parser.add_argument("--output-dir", default=str(HARNESS_ROOT / "results-morph"))
    parser.add_argument("--models", default="base,abliterated")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--task-timeout", type=int, default=5400)
    parser.add_argument("--only", default="", help="comma-separated benchmark ids")
    parser.add_argument("--skip", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    xbow_root = Path(args.xbow_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    config = Path(args.agent_config).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    benchmark_ids = sorted(p.name for p in (xbow_root / "benchmarks").glob("XBEN-*-24"))
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        benchmark_ids = [b for b in benchmark_ids if b in wanted]
    if args.skip:
        unwanted = {item.strip() for item in args.skip.split(",") if item.strip()}
        benchmark_ids = [b for b in benchmark_ids if b not in unwanted]

    agents = [AGENT_NAMES[m.strip()] for m in args.models.split(",") if m.strip() in AGENT_NAMES]
    if not agents:
        parser.error(f"--models must be drawn from {sorted(AGENT_NAMES)}")

    queue: list[tuple[str, str]] = []
    for benchmark_id in benchmark_ids:
        for agent in agents:
            if already_scored(output_dir, benchmark_id, agent):
                continue
            queue.append((benchmark_id, agent))

    log(f"{len(benchmark_ids)} benchmarks x {len(agents)} agents; {len(queue)} runs queued, {args.workers} workers")
    if args.dry_run:
        for benchmark_id, agent in queue:
            print(f"  {benchmark_id} {agent}")
        return 0

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_one, benchmark_id, agent, xbow_root, config, output_dir,
                        args.passes, args.task_timeout): (benchmark_id, agent)
            for benchmark_id, agent in queue
        }
        for future in concurrent.futures.as_completed(futures):
            benchmark_id, agent, returncode, detail = future.result()
            if returncode != 0:
                failures += 1
                log(f"FAIL {benchmark_id} {agent} rc={returncode} {detail}")

    log(f"sweep finished with {failures} failed runs; results under {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
