import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch

from engagement import worker as worker_module
from engagement.api import EngagementProcesses, make_handler
from engagement.models import ACTIVE_STATUSES, EngagementRequest
from engagement.store import EngagementStore
from engagement.worker import EngagementWorker
from tests.test_engagement_foundation import request_dict


class AllowVerifier:
    def verify(self, authorization):
        if authorization != "Bearer synthetic-identity":
            raise AssertionError("unexpected test identity")
        return {"sub": "synthetic"}


class DisabledControlPlaneTest(unittest.TestCase):
    def test_paid_request_runs_isolated_worker_and_returns_safe_failure_without_network(self):
        body = request_dict()
        request = EngagementRequest.from_dict(body)
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=False):
            os.environ["HACK_ME_LAUNCH_ENABLED"] = "0"
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, manager, AllowVerifier()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(
                    "POST", "/v1/engagements", body=json.dumps(body),
                    headers={"Authorization": "Bearer synthetic-identity", "Content-Type": "application/json"},
                )
                response = connection.getresponse()
                accepted = json.loads(response.read())
                self.assertEqual(response.status, 202)
                self.assertEqual(accepted["status"], "queued")
                manager.processes[request.id].wait(timeout=10)

                connection.request(
                    "GET", f"/v1/engagements/{request.id}",
                    headers={"Authorization": "Bearer synthetic-identity"},
                )
                response = connection.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(result["status"], "failed")
                self.assertIn("disabled", result["detail"])
                self.assertNotIn("request", result)
                self.assertNotIn("pid", result)
                self.assertFalse((Path(root) / request.id / ".opencode-password").exists())
                self.assertFalse((Path(root) / request.id / "work").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for process in list(manager.processes.values()):
                    process.wait(timeout=5)


class CancelOverHttpTest(unittest.TestCase):
    """DELETE from landing: cancelled is reported only after confirmed fleet teardown."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.store = EngagementStore(self.root)
        manager = EngagementProcesses(self.store)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, manager, AllowVerifier()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.request = EngagementRequest.from_dict(request_dict())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def call(self, method):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, f"/v1/engagements/{self.request.id}",
                               headers={"Authorization": "Bearer synthetic-identity"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def run_worker_until_cancel_is_noticed(self, teardown):
        worker = EngagementWorker(self.root, self.request.id)

        def execute():
            # The worker's real cancellation point, as in _wait_for_dispatch.
            worker_module._check_cancel(worker.store, worker.request.id, worker.deadline)
            raise AssertionError("cancellation was not noticed")

        with patch.object(worker, "_execute", side_effect=execute), \
             patch.object(worker, "_teardown", side_effect=teardown):
            worker.run()

    def test_delete_on_an_active_engagement_reaches_cancelled_only_after_teardown(self):
        self.store.create(self.request)
        status, body = self.call("DELETE")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "queued")
        self.assertIn("Cancellation requested", body["detail"])
        self.assertEqual(self.call("DELETE"), (status, body))  # idempotent

        # Teardown fails once: still active, and a re-sent DELETE keeps the cleanup detail.
        def failing_teardown():
            self.assertIn(self.store.get(self.request.id)["status"], ACTIVE_STATUSES)
            raise RuntimeError("synthetic teardown failure")

        self.run_worker_until_cancel_is_noticed(failing_teardown)
        status, body = self.call("DELETE")
        self.assertEqual(status, 200)
        self.assertIn(body["status"], ACTIVE_STATUSES)
        self.assertIn("cleanup will be retried", body["detail"])

        # The retried cleanup confirms teardown; only now is it cancelled.
        torn_down = []

        def teardown():
            self.assertIn(self.store.get(self.request.id)["status"], ACTIVE_STATUSES)
            torn_down.append(True)

        with patch.object(EngagementWorker, "_teardown", side_effect=teardown):
            worker_module.cleanup(self.root, self.request.id)
        self.assertEqual(torn_down, [True])
        status, body = self.call("GET")
        self.assertEqual((status, body["status"]), (200, "cancelled"))
        self.assertEqual(body["detail"], "Cancelled; worker fleet teardown completed.")
        self.assertEqual(self.call("DELETE")[1]["status"], "cancelled")

    def test_delete_before_the_launch_arrives_404s_and_the_late_launch_never_runs(self):
        status, _ = self.call("DELETE")
        self.assertEqual(status, 404)
        self.assertEqual(self.store.active(), [])
        # The POST that was in flight lands afterwards.
        self.store.create(self.request)
        worker = EngagementWorker(self.root, self.request.id)
        with patch.object(worker, "_execute") as execute, patch.object(worker, "_teardown") as teardown:
            worker.run()
        execute.assert_not_called()
        teardown.assert_called_once()
        self.assertEqual(self.store.get(self.request.id)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
