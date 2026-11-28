from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from engagement.models import EngagementRequest
from engagement.report import load_report
from engagement.retention import RetentionPolicy
from engagement.store import EngagementStore
from tests.test_engagement_foundation import RUN_KEY, request_dict


def finding(identifier, detail="evidence"):
    item = {name: name for name in ("stream", "worker", "status", "sev", "class", "title", "where", "proof", "control", "chain", "detail")}
    item.update(id=identifier, status="confirmed", sev="info", detail=detail)
    return item


class ReportProjectionTests(unittest.TestCase):
    def test_explicit_supersession_hides_withdrawn_claims_without_schema_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "index.jsonl"
            rows = [
                finding("confirmed-1"),
                finding("withdrawn-low"),
                finding("lead-quarantine", "Supersedes withdrawn-low for its out-of-scope test evidence."),
                finding("old-hypothesis", "Superseded by confirmed-1."),
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            report = load_report(path)
            self.assertEqual([item["id"] for item in report["findings"]], ["confirmed-1", "lead-quarantine"])
            self.assertEqual(set(report), {"findings"})
            self.assertIn("status", report["findings"][0])


class RetentionTests(unittest.TestCase):
    def test_age_based_retention_preserves_terminal_identity_and_never_touches_active_runs(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
            store.create(request)
            store.update(request.id, status="completed", report={"findings": [finding("one")]})
            directory = store._dir(request.id)
            work = directory / "work"
            work.mkdir()
            (work / "session.json").write_text("{}")
            (directory / "worker.log").write_text("test")
            record = store.get(request.id)
            record["updatedAt"] = "2000-01-01T00:00:00Z"
            store._write(directory / "record.json", record)
            active = EngagementRequest.from_dict(request_dict())
            store.create(active)
            now = datetime.now(timezone.utc).timestamp()
            counts = RetentionPolicy(report_seconds=100, evidence_seconds=10).sweep(store, now)
            self.assertEqual(counts, {"retiredEvidence": 1, "retiredReports": 1, "errors": 0})
            self.assertFalse(work.exists())
            self.assertIsNone(store.get(request.id)["report"])
            self.assertEqual(store.get(request.id)["status"], "completed")
            self.assertEqual(store.get(active.id)["status"], "queued")
            self.assertEqual(RetentionPolicy(100, 10).sweep(store, now), {"retiredEvidence": 0, "retiredReports": 0, "errors": 0})

    def test_retention_configuration_fails_closed_on_invalid_values(self):
        for report, evidence in ((0, 10), (10, 0), (-1, 10), (10, "seven days")):
            with self.subTest(report=report, evidence=evidence), self.assertRaises(ValueError):
                RetentionPolicy(report, evidence)


if __name__ == "__main__":
    unittest.main()
