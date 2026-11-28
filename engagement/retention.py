"""Time-box customer findings without ever deleting an active engagement's evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import threading
import uuid

from .models import ACTIVE_STATUSES
from .store import EngagementStore

REPORT_RETENTION_SECONDS = 30 * 24 * 60 * 60
EVIDENCE_RETENTION_SECONDS = 7 * 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 60 * 60


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class RetentionPolicy:
    def __init__(self, report_seconds=REPORT_RETENTION_SECONDS,
                 evidence_seconds=EVIDENCE_RETENTION_SECONDS):
        for name, value in (("report", report_seconds), ("evidence", evidence_seconds)):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} retention must be a positive whole number of seconds")
        self.report_seconds = report_seconds
        self.evidence_seconds = evidence_seconds

    @classmethod
    def from_environment(cls):
        return cls(
            report_seconds=int(os.environ.get("HACK_ME_REPORT_RETENTION_SECONDS", REPORT_RETENTION_SECONDS)),
            evidence_seconds=int(os.environ.get("HACK_ME_EVIDENCE_RETENTION_SECONDS", EVIDENCE_RETENTION_SECONDS)),
        )

    def sweep(self, store: EngagementStore, now: float | None = None) -> dict:
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        counts = {"retiredEvidence": 0, "retiredReports": 0, "errors": 0}
        for directory in store.root.iterdir():
            try:
                engagement_id = str(uuid.UUID(directory.name))
                record = store.get(engagement_id)
            except (ValueError, KeyError):
                continue
            if record["status"] in ACTIVE_STATUSES or record.get("cleanupPending"):
                continue
            try:
                updated = _timestamp(record["updatedAt"])
            except (KeyError, ValueError, TypeError):
                counts["errors"] += 1
                continue
            if now - updated >= self.evidence_seconds:
                # Only the evidence directory moves. Record/idempotency data remains for Landing.
                work = directory / "work"
                if work.exists() and not work.is_symlink():
                    retired = directory / f".retire-{uuid.uuid4().hex}"
                    work.rename(retired)
                    shutil.rmtree(retired)
                    counts["retiredEvidence"] += 1
                for name in ("worker.log", "cleanup.log"):
                    (directory / name).unlink(missing_ok=True)
            if now - updated >= self.report_seconds and record.get("report") is not None:
                with store._lock(engagement_id):
                    current = store.get(engagement_id)
                    if current["status"] not in ACTIVE_STATUSES and not current.get("cleanupPending"):
                        store._write(store._record_path(engagement_id), {**current, "report": None})
                        counts["retiredReports"] += 1
        return counts


def start_sweeper(store: EngagementStore, policy: RetentionPolicy | None = None):
    policy = policy or RetentionPolicy.from_environment()

    def run():
        while True:
            try:
                policy.sweep(store)
            except Exception as exc:  # Keep the control plane healthy; retry next interval.
                print(f"retention sweep deferred: {type(exc).__name__}", file=sys.stderr)
            threading.Event().wait(SWEEP_INTERVAL_SECONDS)

    thread = threading.Thread(target=run, name="engagement-retention", daemon=True)
    thread.start()
    return thread
