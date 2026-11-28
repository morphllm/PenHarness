"""Validate and project harvested findings into landing's small public report."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .models import EngagementError

_FIELDS = ("id", "status", "class", "title", "where", "proof", "control", "chain", "detail")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|access[_-]?token|session|cookie|set-cookie)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def redact(value: object, limit: int = 16_000) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("\\bsk-"):
            value = pattern.sub("[redacted]", value)
        else:
            value = pattern.sub(r"\1[redacted]", value)
    if len(value) > limit:
        raise EngagementError("a finding field exceeds the public report size limit")
    return value


_ID_IN_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _load_findings(path: Path) -> list[dict]:
    items = []
    with path.open() as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EngagementError(f"finding ledger line {line_number} is not valid JSON") from exc
            if not isinstance(item, dict) or any(field not in item for field in (*_FIELDS, "sev")):
                raise EngagementError(f"finding ledger line {line_number} is missing required fields")
            if item["status"] not in {"confirmed", "hypothesis", "dead", "blocked"}:
                raise EngagementError(f"finding ledger line {line_number} has an invalid status")
            if not isinstance(item["id"], str) or not _ID_IN_TEXT.fullmatch(item["id"]):
                raise EngagementError(f"finding ledger line {line_number} has an invalid id")
            items.append(item)
            if len(items) > 501:
                raise EngagementError("the finding ledger exceeds the public report limit")
    return items


def _superseded_ids(items: list[dict]) -> set[str]:
    identifiers = {item["id"] for item in items}
    superseded = set()
    for item in items:
        detail = item["detail"]
        if not isinstance(detail, str):
            continue
        forward = re.search(r"\bSupersedes\s+([^.;]+)", detail)
        if forward:
            references = identifiers.intersection(_ID_IN_TEXT.findall(forward.group(1))) - {item["id"]}
            superseded.update(references)
        backward = re.search(r"\bSuperseded by\s+([^.;]+)", detail)
        if backward:
            superseded.add(item["id"])
    return superseded


def load_report(path: str | Path) -> dict:
    items = _load_findings(Path(path))
    superseded = _superseded_ids(items)
    findings = []
    for item in items:
        if item["id"] in superseded:
            continue
        finding = {field: redact(item[field]) for field in _FIELDS}
        finding["severity"] = redact(item["sev"], 16)
        findings.append(finding)
        if len(findings) > 500:
            raise EngagementError("the finding ledger exceeds the public report limit")
    return {"findings": findings}
