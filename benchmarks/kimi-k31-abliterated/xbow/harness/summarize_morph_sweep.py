#!/usr/bin/env python3
"""Summarize an XBOW sweep: per-task outcome, token use and wall time."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "results-morph")
rows = []
for path in sorted(root.glob("experiments/*/runs/**/result.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    task = data.get("task", {})
    agent = data.get("agent", {})
    score = data.get("score", {})
    result = data.get("result", {})
    metadata = result.get("metadata", {}) or {}
    rows.append(
        {
            "benchmark": task.get("benchmark_id", "?"),
            "agent": agent.get("name", "?"),
            "success": bool(score.get("success")),
            "scored": bool(score.get("scored")),
            "reason": score.get("reason", ""),
            "duration": result.get("duration_seconds") or 0,
            "input_tokens": metadata.get("input_tokens") or 0,
            "output_tokens": metadata.get("output_tokens") or 0,
            "tool_calls": metadata.get("tool_calls_total") or 0,
            "error": metadata.get("error") or "",
        }
    )

if not rows:
    print(f"no results yet under {root}")
    raise SystemExit(0)

by_agent = defaultdict(list)
for row in rows:
    by_agent[row["agent"]].append(row)

print(f"{len(rows)} scored runs under {root}\n")
for agent, agent_rows in sorted(by_agent.items()):
    solved = sum(1 for row in agent_rows if row["success"])
    total_in = sum(row["input_tokens"] for row in agent_rows)
    total_out = sum(row["output_tokens"] for row in agent_rows)
    hours = sum(row["duration"] for row in agent_rows) / 3600
    print(f"{agent}: {solved}/{len(agent_rows)} solved ({100 * solved / len(agent_rows):.1f}%)")
    print(f"  input tokens {total_in:,}  output tokens {total_out:,}  agent wall time {hours:.1f}h")

print("\nsolved:")
for row in sorted(rows, key=lambda r: (not r["success"], r["benchmark"])):
    flag = "PASS" if row["success"] else "miss"
    print(f"  {flag} {row['benchmark']:<12} {row['duration']:7.0f}s  tools={row['tool_calls']:<3} "
          f"in={row['input_tokens']:<8} out={row['output_tokens']:<6} {row['reason'][:60]} {row['error'][:60]}")
