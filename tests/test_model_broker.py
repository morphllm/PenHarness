import http.client
import json
import tempfile
import threading
import time
from pathlib import Path
import unittest

from engagement.api import EngagementProcesses, make_broker_handler, make_handler
from engagement.broker import COORDINATOR_MODEL_ID, MODEL_ID, ModelBroker
from engagement.credential import BROKER_WORKER_PLACEHOLDER, authorize, issue, revoke
from engagement.models import EngagementRequest
from engagement.store import EngagementStore
from tests.test_engagement_foundation import RUN_KEY, request_dict


class AllowVerifier:
    def verify(self, _authorization):
        return {}


class ModelBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EngagementStore(self.temp.name)
        self.request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        self.store.create(self.request)
        self.store.update(self.request.id, status="running")
        (self.store._dir(self.request.id) / ".opencode-password").write_text("synthetic-engagement-token")
        issue(self.store._dir(self.request.id), "synthetic-engagement-token", 60, allowed_ips={"127.0.0.1"})
        self.bodies = []
        def transport(body, _api_key):
            self.bodies.append(json.loads(body))
            return 200, {"Content-Type": "application/json"}, b'{"status":"ok"}'
        self.broker = ModelBroker(self.store, transport=transport)
        self.server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
            ("127.0.0.1", 0), make_broker_handler(self.store, self.broker)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def call(self, body, authorization="Bearer synthetic-engagement-token", request_id=None, port=None):
        connection = http.client.HTTPConnection("127.0.0.1", port or self.server.server_port, timeout=5)
        connection.request(
            "POST", f"/v1/engagements/{request_id or self.request.id}/chat/completions",
            body=json.dumps(body),
            headers={"Content-Type": "application/json", "Authorization": authorization},
        )
        response = connection.getresponse()
        return response.status, response.read()

    def test_broker_allows_only_ablated_within_engagement_limits(self):
        body = {"model": MODEL_ID, "messages": [{"role": "user", "content": "synthetic task"}]}
        status, response = self.call(body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"status": "ok"})
        self.assertEqual(self.bodies[0]["max_tokens"], 32768)
        self.assertEqual(self.bodies[0]["model"], MODEL_ID)
        # There is no request-count cap: the next call goes through. Only dollars stop a run.
        status, _ = self.call(body)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.bodies), 2)
        self.broker.ledger.cap = 1
        status, _ = self.call(body)
        self.assertEqual(status, 402)
        self.assertEqual(len(self.bodies), 2)

    def test_broker_allows_both_models_on_one_ledger(self):
        for model in (COORDINATOR_MODEL_ID, MODEL_ID):
            body = {"model": model, "messages": [{"role": "user", "content": "synthetic task"}]}
            status, _ = self.call(body)
            self.assertEqual(status, 200)
        self.assertEqual([body["model"] for body in self.bodies], [COORDINATOR_MODEL_ID, MODEL_ID])
        ledger = self.broker.ledger.snapshot(self.request.id)
        self.assertEqual(ledger["requestCount"], 2)
        self.assertEqual(
            {entry["model"] for entry in ledger["reservations"].values()},
            {COORDINATOR_MODEL_ID, MODEL_ID},
        )

    def test_broker_rejects_other_models_foreign_tokens_and_large_outputs(self):
        body = {"model": MODEL_ID, "messages": []}
        status, _ = self.call({**body, "model": "invented-model"})
        self.assertEqual(status, 400)
        status, _ = self.call(body, authorization="Bearer wrong")
        self.assertEqual(status, 401)
        status, _ = self.call({**body, "max_tokens": 40000})
        self.assertEqual(status, 400)
        self.assertEqual(self.bodies, [])

    def test_run_deadline_denies_even_with_a_valid_credential(self):
        self.store.update(self.request.id, deadlineAtUnix=time.time() - 1)
        status, _ = self.call({"model": MODEL_ID, "messages": []})
        self.assertEqual(status, 401)
        self.assertEqual(self.bodies, [])

    def test_cancellation_and_terminal_status_block_new_upstream_calls(self):
        body = {"model": MODEL_ID, "messages": []}
        self.store.request_cancel(self.request.id)
        self.assertEqual(self.call(body)[0], 401)
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status=status):
                request = EngagementRequest.from_dict(request_dict())
                self.store.create(request)
                issue(self.store._dir(request.id), "synthetic-engagement-token", 60, allowed_ips={"127.0.0.1"})
                self.store.update(request.id, status=status)
                self.assertEqual(self.call(body, request_id=request.id)[0], 401)
        self.assertEqual(self.bodies, [])

    def test_unknown_broker_requests_leave_state_unchanged(self):
        def snapshot():
            return {path.relative_to(self.temp.name): path.read_bytes() if path.is_file() else None
                    for path in Path(self.temp.name).rglob("*")}
        before = snapshot()
        status, _ = self.call(
            {"model": MODEL_ID, "messages": []},
            request_id=request_dict()["id"],
        )
        self.assertEqual(status, 401)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.bodies, [])

    def test_public_management_listener_does_not_route_model_requests(self):
        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self.store, EngagementProcesses(self.store), AllowVerifier()),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _ = self.call({"model": MODEL_ID, "messages": []}, port=server.server_port)
            self.assertEqual(status, 404)
            self.assertEqual(self.bodies, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class CredentialTests(unittest.TestCase):
    def test_expiry_exact_peer_binding_hash_and_revocation(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            issue(directory, "synthetic-token", 60, allowed_ips={"fdaa:1234::10"}, now=lambda: 1000)
            self.assertTrue(authorize(directory, "synthetic-token", "fdaa:1234::10", now=lambda: 1001))
            self.assertFalse(authorize(directory, "synthetic-token", "fdaa:1234::10", now=lambda: 1060))
            self.assertFalse(authorize(directory, "synthetic-token", "fdaa:1234::11", now=lambda: 1001))
            self.assertFalse(authorize(directory, "wrong-token", "fdaa:1234::10", now=lambda: 1001))
            content = (directory / ".broker-credential.json").read_text()
            self.assertNotIn("synthetic-token", content)
            self.assertNotIn("Bearer", content)
            self.assertNotIn('"allowedIps":[]', content)
            revoke(directory)
            self.assertFalse(authorize(directory, "synthetic-token", "fdaa:1234::10", now=lambda: 1001))

    def test_ttl_is_hard_capped_and_empty_ip_list_denies(self):
        with tempfile.TemporaryDirectory() as root:
            issue(Path(root), "synthetic-token", 60)
            self.assertFalse(authorize(Path(root), "synthetic-token", "fdaa:1234::10", now=time.time))
            with self.assertRaises(ValueError):
                issue(Path(root), "synthetic-token", 21601)

    def test_public_marker_cannot_replace_the_token_hash(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            issue(directory, "controller-secret", 60, allowed_ips={"fdaa:1234::10"}, now=lambda: 1000)
            self.assertTrue(authorize(directory, "controller-secret", "fdaa:1234::10", now=lambda: 1001))
            self.assertFalse(authorize(directory, BROKER_WORKER_PLACEHOLDER, "fdaa:1234::10", now=lambda: 1001))
            self.assertFalse(authorize(directory, BROKER_WORKER_PLACEHOLDER, "fdaa:1234::11", now=lambda: 1001))
            self.assertFalse(authorize(directory, BROKER_WORKER_PLACEHOLDER, "fdaa:1234::10", now=lambda: 1060))



if __name__ == "__main__":
    unittest.main()
