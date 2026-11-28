"""The per-run Morph API key: request shape, 0600 storage, broker use, rotation and deletion."""

import http.client
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch

from engagement import broker as broker_module
from engagement.api import EngagementProcesses, make_broker_handler, make_handler
from engagement.broker import MODEL_ID, ModelBroker
from engagement.credential import issue
from engagement.models import EngagementError, EngagementRequest, public_status
from engagement.store import INFERENCE_KEY_FILE, EngagementStore, StoreConflict
from engagement.worker import EngagementWorker, cleanup as cleanup_engagement
from tests.test_engagement_foundation import RUN_KEY, request_dict

ROTATED_KEY = "sk-" + "R" * 24 + "otated_-" + "9" * 16
TOKEN = "synthetic-engagement-token"


def tree_text(root) -> str:
    """Every file under root except the key file itself, concatenated."""
    parts = []
    for path in Path(root).rglob("*"):
        if path.is_file() and path.name != INFERENCE_KEY_FILE:
            parts.append(path.read_bytes().decode(errors="replace"))
    return "\n".join(parts)


class RequestShapeTests(unittest.TestCase):
    def test_accepts_an_optional_key_and_keeps_it_out_of_the_request(self):
        request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        self.assertEqual(request.inference_api_key, RUN_KEY)
        self.assertNotIn(RUN_KEY, repr(request))
        self.assertNotIn("inference", request.as_dict())
        self.assertNotIn(RUN_KEY, json.dumps(request.as_dict()))
        # The key is not part of the request's identity.
        bare = EngagementRequest.from_dict({**request.as_dict()})
        self.assertIsNone(bare.inference_api_key)
        self.assertEqual(bare, request)

    def test_rejects_malformed_keys_without_echoing_them(self):
        for name, inference in {
            "not an object": "sk-x",
            "empty object": {},
            "extra field": {"apiKey": RUN_KEY, "orgId": "org_1"},
            "wrong field": {"key": RUN_KEY},
            "number": {"apiKey": 12345},
            "null": {"apiKey": None},
            "short": {"apiKey": RUN_KEY[:-1]},
            "long": {"apiKey": RUN_KEY + "a"},
            "wrong prefix": {"apiKey": "pk-" + RUN_KEY[3:]},
            "bad character": {"apiKey": RUN_KEY[:-1] + "!"},
            "trailing newline": {"apiKey": RUN_KEY[:-1] + "\n"},
            "whitespace": {"apiKey": " " + RUN_KEY[1:]},
            "oversized": {"apiKey": "sk-" + "a" * 100_000},
        }.items():
            with self.subTest(name):
                body = request_dict()
                body["inference"] = inference
                with self.assertRaises(EngagementError) as raised:
                    EngagementRequest.from_dict(body)
                if isinstance(inference, dict) and isinstance(inference.get("apiKey"), str):
                    self.assertNotIn(inference["apiKey"].strip(), str(raised.exception))

    def test_still_rejects_other_extra_top_level_fields(self):
        body = request_dict(inference_key=RUN_KEY)
        body["credentials"] = {"apiKey": RUN_KEY}
        with self.assertRaises(EngagementError):
            EngagementRequest.from_dict(body)


class KeyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EngagementStore(self.temp.name)
        self.body = request_dict(inference_key=RUN_KEY)
        self.request = EngagementRequest.from_dict(self.body)
        self.path = self.store._dir(self.request.id) / INFERENCE_KEY_FILE

    def test_key_file_is_0600_and_never_in_record_or_public_status(self):
        accepted = self.store.create(self.request)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.path.read_text(), RUN_KEY)
        self.assertEqual(self.store.inference_key(self.request.id), RUN_KEY)
        record_text = (self.store._dir(self.request.id) / "record.json").read_text()
        self.assertNotIn(RUN_KEY, record_text)
        self.assertNotIn(RUN_KEY, json.dumps(accepted))
        self.assertNotIn(RUN_KEY, json.dumps(public_status(self.store.get(self.request.id))))
        self.assertNotIn(RUN_KEY, tree_text(self.temp.name))
        # No temporary file is left behind.
        self.assertEqual(
            sorted(p.name for p in self.store._dir(self.request.id).iterdir() if p.name.endswith(".tmp")), []
        )

    def test_a_retry_with_a_rotated_key_is_the_same_request_and_the_newest_key_wins(self):
        self.store.create(self.request)
        rotated = EngagementRequest.from_dict({**self.body, "inference": {"apiKey": ROTATED_KEY}})
        self.assertEqual(self.store.create(rotated)["status"], "queued")
        self.assertEqual(self.store.inference_key(self.request.id), ROTATED_KEY)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        # A retry without a key leaves the stored key alone.
        bare = EngagementRequest.from_dict({k: v for k, v in self.body.items() if k != "inference"})
        self.store.create(bare)
        self.assertEqual(self.store.inference_key(self.request.id), ROTATED_KEY)

    def test_a_conflicting_request_never_replaces_the_key(self):
        self.store.create(self.request)
        other = EngagementRequest.from_dict(request_dict(self.request.id, "other.example.com", ROTATED_KEY))
        with self.assertRaises(StoreConflict):
            self.store.create(other)
        self.assertEqual(self.store.inference_key(self.request.id), RUN_KEY)

    def test_no_key_is_installed_on_a_stopping_or_finished_run(self):
        # Tombstoned before the POST arrived: the run starts cancel-requested and gets no key.
        try:
            self.store.request_cancel(self.request.id)
        except KeyError:
            pass
        self.store.create(self.request)
        self.assertIsNone(self.store.inference_key(self.request.id))
        self.assertFalse(self.path.exists())

        finished = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        self.store.create(finished)
        self.store.update(finished.id, status="failed", detail="synthetic")
        self.assertIsNone(self.store.inference_key(finished.id))
        rotated = EngagementRequest.from_dict({**finished.as_dict(), "inference": {"apiKey": ROTATED_KEY}})
        self.store.create(rotated)
        self.assertIsNone(self.store.inference_key(finished.id))

    def test_a_terminal_status_deletes_the_key(self):
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status):
                request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
                self.store.create(request)
                self.store.update(request.id, status="running")
                self.assertEqual(self.store.inference_key(request.id), RUN_KEY)
                self.store.update(request.id, status=status)
                self.assertIsNone(self.store.inference_key(request.id))
                self.assertFalse((self.store._dir(request.id) / INFERENCE_KEY_FILE).exists())

    def test_a_planted_symlink_is_never_read_or_written_through(self):
        self.store.create(EngagementRequest.from_dict({**self.body, "inference": {"apiKey": RUN_KEY}}))
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text(ROTATED_KEY)
        self.path.unlink()
        self.path.symlink_to(outside)
        self.assertIsNone(self.store.inference_key(self.request.id))
        rotated = EngagementRequest.from_dict({**self.body, "inference": {"apiKey": ROTATED_KEY}})
        self.store.create(rotated)
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(outside.read_text(), ROTATED_KEY)
        self.assertEqual(self.store.inference_key(self.request.id), ROTATED_KEY)

    def test_a_malformed_key_file_reads_as_no_key(self):
        self.store.create(self.request)
        self.path.write_text("not-a-key")
        self.assertIsNone(self.store.inference_key(self.request.id))


class BrokerKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EngagementStore(self.temp.name)
        self.body = request_dict(inference_key=RUN_KEY)
        self.request = EngagementRequest.from_dict(self.body)
        self.store.create(self.request)
        self.store.update(self.request.id, status="running")
        issue(self.store._dir(self.request.id), TOKEN, 60, allowed_ips={"127.0.0.1"})
        self.upstream = []

        def transport(body, api_key):
            self.upstream.append({"body": json.loads(body), "apiKey": api_key})
            return 200, {"Content-Type": "application/json"}, b'{"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1}}'

        self.broker = ModelBroker(self.store, transport=transport, environ={})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_broker_handler(self.store, self.broker))
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)

    def call(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(
                "POST", f"/v1/engagements/{self.request.id}/chat/completions",
                body=json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
            )
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_the_broker_sends_this_runs_key_upstream_and_follows_a_rotation(self):
        status, data = self.call()
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream[-1]["apiKey"], RUN_KEY)
        self.assertNotIn(RUN_KEY, data.decode())
        self.store.create(EngagementRequest.from_dict({**self.body, "inference": {"apiKey": ROTATED_KEY}}))
        self.assertEqual(self.call()[0], 200)
        self.assertEqual(self.upstream[-1]["apiKey"], ROTATED_KEY)
        # The ledger records calls, never the key.
        self.assertNotIn(RUN_KEY, tree_text(self.temp.name))
        self.assertNotIn(ROTATED_KEY, tree_text(self.temp.name))

    def test_no_run_key_refuses_the_paid_call_with_503_and_never_falls_back(self):
        self.store.delete_inference_key(self.request.id)
        with patch.dict(os.environ, {"MORPH_API_KEY": "sk-" + "S" * 48}):
            status, data = self.call()
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["error"]["type"], "credential_unavailable")
        self.assertEqual(self.upstream, [])
        # Nothing was reserved against the engagement's budget.
        self.assertEqual(self.broker.ledger.snapshot(self.request.id)["requestCount"], 0)

    def test_the_http_transport_authorizes_with_the_given_key_not_the_environment(self):
        sent = {}

        class FakeConnection:
            def __init__(self, host, port, timeout):
                sent["host"] = host
                self.sock = None

            def connect(self):
                pass

            def request(self, method, path, body, headers):
                sent.update(method=method, path=path, headers=headers)

            def getresponse(self):
                class Response:
                    status = 200

                    def getheaders(self):
                        return [("Content-Type", "application/json")]
                return Response()

            def close(self):
                pass

        environment = {"HACK_ME_MORPH_BASE_URL": "https://api.morphllm.com", "MORPH_API_KEY": "sk-" + "S" * 48}
        with patch.dict(os.environ, environment, clear=True), \
             patch.object(broker_module.http.client, "HTTPSConnection", FakeConnection):
            status, _headers, upstream = ModelBroker._http_transport(b"{}", RUN_KEY)
        self.assertEqual(status, 200)
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {RUN_KEY}")
        self.assertEqual((sent["host"], sent["path"]), ("api.morphllm.com", "/v1/chat/completions"))
        upstream.close()


class KeyTeardownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.store = EngagementStore(self.root)

    def engagement(self):
        request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        self.store.create(request)
        return request

    def test_the_worker_deletes_the_key_after_teardown(self):
        request = self.engagement()
        worker = EngagementWorker(self.root, request.id)
        torn_down = []

        def teardown():
            # Teardown runs while the key still exists; deletion follows it.
            torn_down.append(self.store.inference_key(request.id))

        with patch.object(worker, "_execute", side_effect=EngagementError("synthetic stop")), \
             patch.object(worker, "_teardown", side_effect=teardown):
            worker.run()
        self.assertEqual(torn_down, [RUN_KEY])
        self.assertIsNone(self.store.inference_key(request.id))
        self.assertEqual(self.store.get(request.id)["status"], "failed")

    def test_a_failed_teardown_still_removes_the_key_and_leaves_cleanup_pending(self):
        request = self.engagement()
        worker = EngagementWorker(self.root, request.id)
        with patch.object(worker, "_execute", side_effect=EngagementError("synthetic stop")), \
             patch.object(worker, "_teardown", side_effect=RuntimeError("synthetic teardown failure")), \
             patch("traceback.print_exception"):
            worker.run()
        record = self.store.get(request.id)
        self.assertTrue(record["cleanupPending"])
        self.assertIsNone(self.store.inference_key(request.id))

    def test_cleanup_deletes_the_key(self):
        request = self.engagement()
        with patch.object(EngagementWorker, "_teardown", return_value=None):
            cleanup_engagement(self.root, request.id)
        self.assertIsNone(self.store.inference_key(request.id))
        self.assertEqual(self.store.get(request.id)["status"], "failed")

    def test_cancel_keeps_the_key_until_teardown_is_confirmed(self):
        request = self.engagement()
        self.store.request_cancel(request.id)
        # Still stopping: the broker refuses calls (cancel-requested) but the file stays until
        # the fleet is confirmed down, when the terminal status removes it.
        self.assertEqual(self.store.inference_key(request.id), RUN_KEY)
        worker = EngagementWorker(self.root, request.id)
        with patch.object(worker, "_teardown", return_value=None):
            worker.run()
        self.assertEqual(self.store.get(request.id)["status"], "cancelled")
        self.assertIsNone(self.store.inference_key(request.id))

    def test_the_worker_refuses_to_launch_without_a_run_key(self):
        request = EngagementRequest.from_dict(request_dict())
        self.store.create(request)
        worker = EngagementWorker(self.root, request.id)
        env = {
            "HACK_ME_LAUNCH_ENABLED": "1", "HACK_ME_EGRESS_ENFORCED": "1",
            "HACK_ME_EGRESS_PROXY_URL": "http://127.0.0.1:1",
            "HACK_ME_MODEL_BROKER_ORIGIN": "http://fixture.internal:8081",
            "MORPH_API_KEY": "sk-" + "S" * 48,
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("engagement.worker.verify_ownership") as verify, \
             patch.object(worker, "_teardown", return_value=None):
            worker.run()
        verify.assert_not_called()
        record = self.store.get(request.id)
        self.assertEqual(record["status"], "failed")
        self.assertIn("no inference credential", record["detail"])


class AllowVerifier:
    def verify(self, authorization):
        if authorization != "Bearer synthetic-identity":
            raise AssertionError("unexpected test identity")
        return {"sub": "synthetic"}


class KeyOverHttpTests(unittest.TestCase):
    def test_post_stores_the_key_and_no_response_carries_it(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store, manager, AllowVerifier()))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = request_dict(inference_key=RUN_KEY)
                responses = []
                with patch.object(manager, "_spawn"):
                    for key in (RUN_KEY, ROTATED_KEY):
                        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                        connection.request(
                            "POST", "/v1/engagements", body=json.dumps({**body, "inference": {"apiKey": key}}),
                            headers={"Authorization": "Bearer synthetic-identity", "Content-Type": "application/json"},
                        )
                        response = connection.getresponse()
                        responses.append((response.status, response.read().decode()))
                        connection.close()
                    malformed = {**body, "inference": {"apiKey": RUN_KEY + "x"}}
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request(
                        "POST", "/v1/engagements", body=json.dumps(malformed),
                        headers={"Authorization": "Bearer synthetic-identity", "Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    responses.append((response.status, response.read().decode()))
                    connection.close()
                self.assertEqual([status for status, _ in responses], [202, 202, 400])
                for _, text in responses:
                    self.assertNotIn(RUN_KEY, text)
                    self.assertNotIn(ROTATED_KEY, text)
                self.assertEqual(store.inference_key(body["id"]), ROTATED_KEY)
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request("GET", f"/v1/engagements/{body['id']}", headers={"Authorization": "Bearer synthetic-identity"})
                status_text = connection.getresponse().read().decode()
                connection.close()
                self.assertNotIn(ROTATED_KEY, status_text)
                self.assertNotIn(ROTATED_KEY, tree_text(root))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
