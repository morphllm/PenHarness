#!/usr/bin/env python3
"""Mask host environment dumps inside captured agent transcripts.

The agent jail inherits the invoking shell's environment, so when an agent runs
`printenv` or `env` the trace captures this box's whole environment — Cloudflare,
Twilio and other account tokens included — and GitHub push protection
(correctly) refuses the push.

This rewrites only tool-output blobs that look like an environment dump: values
are replaced with `<redacted>` except for the operating-system variables below.
Model output, commands, and everything else are untouched.

Usage: python3 sanitize_transcripts.py tasks tasks-dsv41
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

KEEP = {"SHELL", "PATH", "HOME", "PWD", "TERM", "LANG", "LC_ALL", "USER", "LOGNAME", "TMPDIR"}


def looks_like_env_dump(text: str) -> bool:
    lines = [line for line in text.split("\n") if "=" in line and not line.startswith(" ")]
    return len(lines) > 10


def sanitize(dump: str) -> str:
    out = []
    for line in dump.split("\n"):
        if "=" not in line:
            out.append(line)
            continue
        key, value = line.split("=", 1)
        out.append(f"{key}={value}" if key in KEEP else f"{key}=<redacted>")
    return "\n".join(out)


def process(path: Path) -> int:
    changed = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        state = part.get("state") or {}
        output = state.get("output")
        if isinstance(output, str) and looks_like_env_dump(output):
            state["output"] = sanitize(output)
            metadata = state.get("metadata") or {}
            if metadata.get("output") == output:
                metadata["output"] = state["output"]
            lines[i] = json.dumps(event)
            changed += 1
    if changed:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    total = 0
    files = 0
    for directory in sys.argv[1:]:
        for path in sorted(Path(directory).glob("**/opencode_transcript.jsonl")):
            changed = process(path)
            if changed:
                files += 1
                total += changed
                print(f"{path}: {changed} blob(s) masked")
    print(f"masked {total} env dump(s) across {files} transcript(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
