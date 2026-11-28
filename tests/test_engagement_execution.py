import argparse
import importlib.util
import fleet
import json
import multiprocessing
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from engagement.api import EngagementCapacity, EngagementProcesses
from engagement.credential import credential_path, issue
from engagement.models import ACTIVE_STATUSES, EngagementError, EngagementRequest
from engagement.report import load_report, redact
from engagement.store import EngagementStore
from engagement.worker import (
    EngagementCancelled,
    EngagementWorker,
    cleanup as cleanup_engagement,
    working_directory,
)
from tests.test_engagement_foundation import RUN_KEY, request_dict


def admit_fixture(root, start, result):
    manager = EngagementProcesses(EngagementStore(root))
    request = EngagementRequest.from_dict(request_dict())
    with patch.dict(os.environ, {"HACK_ME_MAX_ACTIVE": "1"}), patch.object(manager, "_spawn"):
        start.wait(5)
        try:
            manager.launch(request)
        except EngagementCapacity:
            result.put(False)
        else:
            result.put(True)


class ControlPlaneTransportTests(unittest.TestCase):
    def test_management_and_private_worker_calls_bypass_scoped_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps({"path": self.path}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_port
        try:
            env = {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "OPENCODE_SERVER_PASSWORD": "synthetic-worker-password",
                "FLY_API_TOKEN": "synthetic-management-token",
                "OPENCODE_PORT": str(port),
            }
            with patch.dict(fleet.os.environ, env, clear=True), \
                 patch.object(fleet, "MACHINES_API", f"http://127.0.0.1:{port}/v1"):
                self.assertEqual(fleet.mapi("GET", "/health"), (200, {"path": "/v1/health"}))
                result = fleet.box_req(
                    {"ip": "127.0.0.1", "privateOnly": True}, "GET", "/private-health"
                )
            self.assertEqual(result, (200, {"path": "/private-health"}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class BrokerSmokeTests(unittest.TestCase):
    def test_smoke_requires_actual_Abliterated_chat_content(self):
        spec = importlib.util.spec_from_file_location(
            "paid_worker_broker_smoke", Path("fly/broker-smoke.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        require_ack = module.require_ack
        self.assertIsNone(require_ack(200, {
            "choices": [{"message": {"content": "ok"}}]
        }))
        for status, payload in (
            (200, {"error": {"message": "provider error"}}),
            (200, {"choices": []}),
            (503, {"choices": [{"message": {"content": "ok"}}]}),
        ):
            with self.assertRaises(RuntimeError):
                require_ack(status, payload)


    def test_smoke_refuses_provider_and_management_credentials_first(self):
        spec = importlib.util.spec_from_file_location(
            "paid_worker_broker_smoke", Path("fly/broker-smoke.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name in ('MORPH_API_KEY', 'FLY_API_TOKEN'):
            with patch.dict(os.environ, {name: 'synthetic-credential'}, clear=True):
                with self.assertRaisesRegex(RuntimeError, name):
                    module.main()


class ReportTests(unittest.TestCase):
    def test_harvest_is_projected_and_credentials_are_redacted(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "index.jsonl"
            item = {
                "id": "HM-01", "stream": "surface", "worker": "worker-1",
                "status": "confirmed", "sev": "high", "class": "authz", "title": "Owned object scope mismatch",
                "where": "app.example.com/v1/items", "proof": "Authorization: Bearer synthetic-secret",
                "control": "different owned object denied", "chain": "", "detail": "details.md",
            }
            ledger.write_text(json.dumps(item) + "\n")
            report = load_report(ledger)
            finding = report["findings"][0]
            self.assertEqual(finding["severity"], "high")
            self.assertNotIn("synthetic-secret", finding["proof"])
            self.assertNotIn("worker", finding)
            self.assertNotIn("stream", finding)

    def test_malformed_ledger_is_an_error_not_a_partial_report(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "index.jsonl"
            ledger.write_text('{"status":"confirmed"}\nnot-json\n')
            with self.assertRaises(EngagementError):
                load_report(ledger)

    def test_morph_key_shape_is_redacted(self):
        self.assertNotIn("sk-synthetic-key", redact("key=sk-synthetic-key"))


class WorkerTests(unittest.TestCase):
    def _worker(self, root):
        request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        store = EngagementStore(root)
        store.create(request)
        return EngagementWorker(root, request.id)

    def test_launch_gate_gates_before_hunt_or_password_creation(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            with patch("engagement.worker.public_addresses", return_value={"93.184.216.34"}), \
                 patch.object(worker, "_teardown", return_value=None):
                worker.run()
            record = worker.store.get(worker.request.id)
            self.assertEqual(record["status"], "failed")
            self.assertIn("disabled", record["detail"])
            self.assertFalse(worker.password_path.exists())
            self.assertFalse(worker.work.exists())

    def test_final_activity_keeps_native_tasks_planned(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            (worker.work / "findings.d").mkdir(parents=True)
            (worker.work / "findings.d" / "index.jsonl").write_text("")
            worker.completed_tasks = 2
            worker.update_activity("logic", 2)
            worker.record_result_counts(0)  # Native mode has no remote dispatched list.
            activity = worker.store.get(worker.request.id)["activity"]
            assert activity is not None
            self.assertEqual(activity["tasksPlanned"], 2)
            self.assertEqual(activity["tasksCompleted"], 2)


    def test_landing_and_controller_checks_are_not_duplicated_before_machine_preflight(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            env = {
                "HACK_ME_LAUNCH_ENABLED": "1", "HACK_ME_EGRESS_ENFORCED": "1",
                "HACK_ME_EGRESS_PROXY_URL": "http://127.0.0.1:1",
                "HACK_ME_MODEL_BROKER_ORIGIN": "http://fixture.internal:8081",
                "MORPH_API_KEY": "synthetic-test-value",
            }
            with patch.dict(os.environ, env, clear=True), \
                 patch("engagement.worker.verify_ownership", side_effect=AssertionError("DNS was re-requested")) as verify, \
                 patch("engagement.worker.public_addresses", return_value={"93.184.216.34"}) as resolve, \
                 patch.object(fleet, "cmd_up", side_effect=EngagementError("stop before machines")) as up:
                worker.run()
            verify.assert_not_called()
            resolve.assert_not_called()
            up.assert_not_called()
            self.assertTrue(worker.work.exists())
            self.assertFalse(worker.password_path.exists())
            self.assertEqual(worker.store.get(worker.request.id)["status"], "failed")

    def test_worker_sends_the_issued_broker_credential_not_the_placeholder(self):
        from engagement.credential import BROKER_WORKER_PLACEHOLDER, allow_workers, authorize
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            directory = worker.store._dir(worker.request.id)
            env = {
                "HACK_ME_LAUNCH_ENABLED": "1", "HACK_ME_EGRESS_ENFORCED": "1",
                "HACK_ME_EGRESS_PROXY_URL": "http://127.0.0.1:1",
                "HACK_ME_MODEL_BROKER_ORIGIN": "http://fixture.internal:8081",
                "MORPH_API_KEY": "synthetic-test-value",
                "FLY_PRIVATE_IP": "fdaa:0:1::2",
            }
            seen = {}

            def provision(_args):
                # Everything after credential setup is mocked: inspect what workers would get.
                seen["token"] = os.environ["HACK_ME_BROKER_TOKEN"]
                seen["password"] = os.environ["OPENCODE_SERVER_PASSWORD"]
                seen["passwordFile"] = worker.password_path.read_text()
                seen["workerEnv"] = fleet.worker_inference_environment()
                allow_workers(directory, ["fdaa:0:1::10"])
                seen["tokenAuthorized"] = authorize(directory, seen["token"], "fdaa:0:1::10")
                seen["passwordAuthorized"] = authorize(directory, seen["password"], "fdaa:0:1::10")
                seen["placeholderAuthorized"] = authorize(directory, BROKER_WORKER_PLACEHOLDER, "fdaa:0:1::10")
                raise EngagementError("stop after credential setup")

            with patch.dict(os.environ, env, clear=True), \
                 patch("engagement.worker.verify_ownership"), \
                 patch("engagement.worker.public_addresses", return_value={"93.184.216.34"}), \
                 patch.object(fleet, "cmd_up", side_effect=provision) as up, \
                 patch.object(worker, "_teardown", return_value=None):
                worker.run()
            up.assert_called_once()
            self.assertNotEqual(seen["token"], BROKER_WORKER_PLACEHOLDER)
            self.assertGreaterEqual(len(seen["token"]), 32)
            self.assertTrue(seen["tokenAuthorized"])
            self.assertFalse(seen["placeholderAuthorized"])
            # The broker secret is distinct from the OpenCode server password.
            self.assertNotEqual(seen["token"], seen["password"])
            self.assertFalse(seen["passwordAuthorized"])
            self.assertEqual(seen["passwordFile"], seen["password"])
            self.assertEqual(seen["workerEnv"]["HACK_ME_BROKER_TOKEN"], seen["token"])
            self.assertEqual(
                seen["workerEnv"]["HACK_ME_MODEL_BROKER_URL"],
                f"http://fixture.internal:8081/v1/engagements/{worker.request.id}",
            )
            # Revocation removes every credential the run issued.
            self.assertFalse(worker.password_path.exists())
            self.assertFalse(credential_path(directory).exists())
            self.assertEqual(worker.store.get(worker.request.id)["status"], "failed")

    def test_fly_worker_environment_preserves_scoped_proxy_and_blank_no_proxy(self):
        env = {
            "HTTP_PROXY": "http://10.0.0.8:3128",
            "NO_PROXY": "",
            "http_proxy": "http://10.0.0.8:3128",
            "no_proxy": "",
        }
        with patch.dict(fleet.os.environ, env, clear=True):
            result = fleet.scoped_proxy_environment({"PORT": "4096"})
        self.assertEqual(result, {"PORT": "4096", **env})


    def test_paid_worker_environment_includes_required_egress_controls_only(self):
        env = {name: f"synthetic-{name}" for name in fleet.PAID_WORKER_CONFIG_FIELDS}
        env["FLY_API_TOKEN"] = "synthetic-management-token-not-for-workers"
        with patch.dict(fleet.os.environ, env, clear=True):
            result = fleet.paid_worker_environment({"PORT": "4096"})
        expected = {"PORT": "4096", **{name: env[name] for name in fleet.PAID_WORKER_CONFIG_FIELDS}}
        self.assertEqual(result, expected)
        self.assertNotIn("FLY_API_TOKEN", result)

    def test_controller_image_source_app_is_separate_from_worker_app(self):
        with patch.dict(fleet.os.environ, {"HACK_ME_CONTROL_FLY_APP": "controller-app"}, clear=True):
            self.assertEqual(fleet.image_source_app("worker-app"), "controller-app")
        with patch.dict(fleet.os.environ, {}, clear=True):
            self.assertEqual(fleet.image_source_app("worker-app"), "worker-app")

    def test_wait_for_dispatch_counts_only_the_current_stream(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.store.update(worker.request.id, status="running")
            worker.completed_tasks = 3
            state = {
                "machines": [{"name": "m1"}],
                "dispatched": [
                    {"stream": "cves", "machine": "m1", "session": "s1"},
                    {"stream": "chains", "machine": "m1", "session": "s2"},
                ],
            }
            with patch("engagement.worker.fleet.load_state", return_value=state), \
                 patch("engagement.worker.fleet._lane_busy", return_value=False), \
                 patch("engagement.worker.fleet.reject_pending_permissions", return_value=0) as reject:
                from engagement.worker import _wait_for_dispatch
                _wait_for_dispatch(worker, "cves", "cves", 26)
            activity = worker.store.get(worker.request.id)["activity"]
            self.assertEqual(activity["tasksCompleted"], 4)
            self.assertEqual(activity["cveProbesCompleted"], 1)
            self.assertEqual(activity["chainsCompleted"], 0)
            self.assertEqual(activity["phase"], "cves")
            reject.assert_called_once_with({"name": "m1"}, "s1")

    def test_unattended_permission_prompts_are_rejected_never_approved(self):
        pending = [
            {"id": "per_a", "sessionID": "s1", "permission": "external_directory"},
            {"id": "per_b", "sessionID": "other", "permission": "bash"},
        ]
        calls = []

        def box(machine, method, path, body=None, timeout=0):
            calls.append((method, path, body))
            return 200, (pending if method == "GET" else None)

        with patch.object(fleet, "box_req", side_effect=box):
            self.assertEqual(fleet.reject_pending_permissions({"name": "m1"}, "s1"), 1)
        replies = [c for c in calls if c[0] == "POST"]
        self.assertEqual([c[1] for c in replies], ["/permission/per_a/reply"])
        self.assertEqual(replies[0][2]["reply"], "reject")
        with patch.object(fleet, "box_req", side_effect=fleet.FleetError("down")):
            self.assertEqual(fleet.reject_pending_permissions({"name": "m1"}, "s1"), 0)

    def test_paid_worker_environment_never_contains_the_upstream_provider_key(self):
        env = {
            "HACK_ME_MODEL_BROKER_URL": "https://broker.example/v1/engagements/id",
            "HACK_ME_BROKER_TOKEN": "synthetic-engagement-token",
            "MORPH_API_KEY": "synthetic-provider-key-that-must-stay-on-control-plane",
        }
        with patch.dict(fleet.os.environ, env, clear=True):
            result = fleet.worker_inference_environment()
        self.assertNotIn("MORPH_API_KEY", result)
        self.assertEqual(result["HACK_ME_MODEL_BROKER_URL"], env["HACK_ME_MODEL_BROKER_URL"])
        self.assertEqual(result["HACK_ME_BROKER_TOKEN"], env["HACK_ME_BROKER_TOKEN"])
        self.assertNotIn(env["MORPH_API_KEY"], str(result))


    def test_paid_workers_are_private_only_and_model_calls_use_private_address(self):
        with patch.dict(fleet.os.environ, {"HACK_ME_PAID_ENGAGEMENT": "1"}, clear=True):
            self.assertEqual(fleet.fly_worker_services(), [])
        with patch.dict(fleet.os.environ, {"OPENCODE_SERVER_PASSWORD": "synthetic-test"}, clear=True), \
             patch.object(fleet, "_request", return_value=(200, {})) as request:
            fleet.box_req({"instance": "public-id", "ip": "fdaa:1234::10", "privateOnly": True}, "GET", "/health")
        self.assertEqual(request.call_args.args[0], "http://[fdaa:1234::10]:4096/health")

    def test_teardown_retries_force_after_harvest_failure(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            attempts = []
            def down(args):
                attempts.append(args.force)
                if len(attempts) == 1:
                    raise SystemExit(1)
            worker.password_path.write_text("synthetic-test-password")
            with patch("engagement.worker.fleet.cmd_down", side_effect=down), \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("OPENCODE_SERVER_PASSWORD", None)
                worker._teardown()
            self.assertEqual(attempts, [False, True])

    def test_cancellation_teardown_failure_remains_recoverable(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            worker.password_path.write_text("synthetic-test-password")
            issue(worker.store._dir(worker.request.id), "synthetic-test-password", 60)
            worker.store.request_cancel(worker.request.id)
            with patch.object(worker, "_execute", side_effect=EngagementCancelled()), \
                 patch.object(worker, "_teardown", side_effect=RuntimeError("synthetic teardown failure")):
                worker.run()
            record = worker.store.get(worker.request.id)
            self.assertIn(record["status"], ACTIVE_STATUSES)
            self.assertTrue(record["cleanupPending"])
            self.assertFalse(worker.password_path.exists())
            self.assertFalse(credential_path(worker.store._dir(worker.request.id)).exists())
            self.assertIn(worker.request.id, {item["id"] for item in worker.store.active()})
            with patch.object(worker, "_teardown", return_value=None):
                cleanup_engagement(root, worker.request.id)
            recovered = worker.store.get(worker.request.id)
            self.assertEqual(recovered["status"], "cancelled")
            self.assertFalse(recovered["cleanupPending"])

    def test_partial_teardown_preserves_only_unresolved_machines(self):
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)):
            machines = [{"id": "one", "name": "worker-one"}, {"id": "two", "name": "worker-two"}]
            fleet.save_state({
                "machines": machines,
                "lanes": {"one": {"machine": "worker-one"}, "two": {"machine": "worker-two"}},
                "dispatched": [],
            })
            def first_destroy(machine):
                if machine["id"] == "two":
                    raise fleet.FleetError("synthetic failure")
                return machine["name"]
            with patch("engagement.worker.fleet.destroy_machine", side_effect=first_destroy):
                with self.assertRaises(fleet.FleetError):
                    fleet.cmd_down(argparse.Namespace(force=True, out="."))
            self.assertEqual(fleet.load_state()["machines"], [machines[1]])
            self.assertEqual(fleet.load_state()["lanes"], {"two": {"machine": "worker-two"}})
            with patch("engagement.worker.fleet.destroy_machine", return_value="worker-two"):
                fleet.cmd_down(argparse.Namespace(force=True, out="."))
            self.assertEqual(fleet.load_state()["machines"], [])
            self.assertEqual(fleet.load_state()["lanes"], {})

    def test_destroy_is_idempotent_when_machine_is_already_absent(self):
        with patch.dict(fleet.os.environ, {"FLY_APP": "synthetic-worker-app"}), \
             patch("engagement.worker.fleet.mapi", side_effect=fleet.FleetError("gone", status=404)):
            self.assertEqual(
                fleet.destroy_machine({"id": "test-machine", "name": "test-worker"}),
                "test-worker",
            )

    def test_early_worker_exit_runs_cleanup_to_a_terminal_record(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            manager = EngagementProcesses(worker.store)
            cleanup_finished = threading.Event()
            def cleanup_spawn(request_id, cleanup=False):
                if cleanup:
                    cleanup_engagement(root, request_id)
                    cleanup_finished.set()
            class ExitedProcess:
                def wait(self, timeout):
                    return 19
            with patch.object(worker, "_teardown", return_value=None), \
                 patch.object(manager, "_spawn", side_effect=cleanup_spawn):
                manager._track(worker.request.id, ExitedProcess())
                self.assertTrue(cleanup_finished.wait(2))
            record = worker.store.get(worker.request.id)
            self.assertEqual(record["status"], "failed")
            self.assertFalse(record["cleanupPending"])

    def test_cleanup_early_exit_persists_pending_and_schedules_retry(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            manager = EngagementProcesses(worker.store)
            class CleanupDiedBeforeBookkeeping:
                def wait(self, timeout):
                    return 13
            with patch("engagement.worker.EngagementWorker._teardown", return_value=None):
                manager._track(worker.request.id, CleanupDiedBeforeBookkeeping(), cleanup=True)
                deadline = threading.Event()
                for _ in range(200):
                    if worker.store.get(worker.request.id).get("cleanupPending"):
                        break
                    deadline.wait(0.01)
            record = worker.store.get(worker.request.id)
            self.assertTrue(record["cleanupPending"])
            self.assertIn(record["status"], ACTIVE_STATUSES)
            self.assertIn(worker.request.id, manager.retry_timers)
            for timer in manager.retry_timers.values():
                timer.cancel()

    def test_reconcile_retries_when_process_identity_cannot_be_checked(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            worker.store.update(worker.request.id, pid=os.getpid(), processIdentity="synthetic-old-identity")
            manager = EngagementProcesses(worker.store)
            with patch.object(manager, "_identity", return_value=None), \
                 patch.object(manager, "_spawn", side_effect=AssertionError("must not clean a possibly live worker")):
                manager._reconcile(worker.request.id)
            self.assertIn(worker.store.get(worker.request.id)["status"], ACTIVE_STATUSES)
            self.assertIn(worker.request.id, manager.retry_timers)
            for timer in manager.retry_timers.values():
                timer.cancel()

    def test_recovery_adopts_after_an_initial_identity_inspection_failure(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            worker.store.update(
                worker.request.id, pid=424242, processCleanup=False, processIdentity=None
            )
            manager = EngagementProcesses(worker.store)
            digest = "abc123"
            with patch.object(manager, "_identity", return_value=digest), \
                 patch.object(manager, "_track", return_value=None), \
                 patch.object(manager, "_spawn", side_effect=AssertionError("must not clean the verified live worker")):
                manager._reconcile(worker.request.id)
            record = worker.store.get(worker.request.id)
            self.assertEqual(record["processIdentity"], digest)
            self.assertEqual(record["pid"], 424242)
            self.assertFalse(record["processCleanup"])

    def test_no_work_cleanup_spawn_failure_is_persisted_for_retry(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            manager = EngagementProcesses(worker.store)
            with patch.object(manager, "_spawn", side_effect=OSError("synthetic spawn failure")):
                manager._reconcile(worker.request.id)
            record = worker.store.get(worker.request.id)
            self.assertTrue(record["cleanupPending"])
            self.assertIn(record["status"], ACTIVE_STATUSES)
            self.assertIn(worker.request.id, manager.retry_timers)
            for timer in manager.retry_timers.values():
                timer.cancel()

    def test_concurrent_distinct_launches_respect_global_capacity(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"HACK_ME_MAX_ACTIVE": "1"}):
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            requests = [EngagementRequest.from_dict(request_dict()) for _ in range(8)]
            def launch(request):
                try:
                    manager.launch(request)
                    return True
                except EngagementCapacity:
                    return False
            with patch.object(manager, "_spawn", return_value=None), ThreadPoolExecutor(max_workers=8) as pool:
                accepted = list(pool.map(launch, requests))
            self.assertEqual(sum(accepted), 1)
            self.assertEqual(len(store.active()), 1)

    def test_independent_controller_processes_share_one_capacity_slot(self):
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as root:
            start, result = ctx.Event(), ctx.Queue()
            children = [ctx.Process(target=admit_fixture, args=(root, start, result)) for _ in range(3)]
            try:
                for child in children:
                    child.start()
                start.set()
                self.assertEqual(sorted(result.get(timeout=10) for _ in children), [False, False, True])
                for child in children:
                    child.join(5)
                    self.assertEqual(child.exitcode, 0)
                self.assertEqual(len(EngagementStore(root).active()), 1)
            finally:
                for child in children:
                    if child.is_alive():
                        child.terminate()
                        child.join(5)
                result.close()

    def test_unconfirmed_allocation_needs_exact_name_and_engagement_to_clean(self):
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, {"FLY_APP": "fixture"}):
            intent = {"app": "fixture", "name": "one", "engagement": "owner"}
            fleet.save_state({"machines": [], "cleanupMachines": {"one": intent}})
            other = {"id": "foreign", "name": "one", "config": {"metadata": {"engagement_id": "another"}}}
            own = {"id": "owned", "name": "one", "config": {"metadata": {"engagement_id": "owner"}}}
            with patch.object(fleet, "mapi", side_effect=[(200, [other]), (200, [other, own])]), patch.object(fleet, "destroy_machine") as destroy:
                with self.assertRaises(fleet.FleetError):
                    fleet.cmd_down(argparse.Namespace(force=True))
                destroy.assert_not_called()
                kept = fleet.load_state()["cleanupMachines"]["one"]
                self.assertEqual({k: kept[k] for k in intent}, intent)
                self.assertEqual(kept["misses"], 1)
                fleet.cmd_down(argparse.Namespace(force=True))
                destroy.assert_called_once_with({"id": "owned", "name": "one"})
                self.assertEqual(fleet.load_state()["cleanupMachines"], {})

    def _rejected_create_then_down(self, failure):
        """Run an engagement create that fails with `failure`, then one cmd_down against an empty Fly listing."""
        env = {"FLY_APP": "fixture-app", "OPENCODE_SERVER_PASSWORD": "synthetic", "FLY_API_TOKEN": "synthetic",
               "HACK_ME_PAID_ENGAGEMENT": "1"}
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, env, clear=True):
            up = argparse.Namespace(count=1, backend="fly", type=None, ttl=3600, from_snapshot=None,
                                    image=None, region="iad", cpus=2, memory=2048, prefix="hack-me",
                                    role="worker", skip_health=False, engagement_id="eng")
            with patch.object(fleet, "resolve_image", return_value="img"), \
                 patch.object(fleet, "require_isolated_worker_app"), \
                 patch.object(fleet, "mapi", side_effect=failure) as create:
                try:
                    fleet.cmd_up(up)
                except (fleet.FleetError, SystemExit):
                    pass
            self.assertEqual(create.call_args.args[:2], ("POST", "/apps/fixture-app/machines"))
            journal = fleet.load_state().get("cleanupMachines", {})
            with patch.object(fleet, "mapi", return_value=(200, [])), \
                 patch.object(fleet, "destroy_machine") as destroy, \
                 patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                try:
                    fleet.cmd_down(argparse.Namespace(force=True, out="."))
                    outcome = out.getvalue()
                except fleet.FleetError as exc:
                    outcome = str(exc)
            destroy.assert_not_called()
            return journal, outcome

    def test_definitively_rejected_create_drops_its_intent_and_cleanup_converges(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                journal, outcome = self._rejected_create_then_down(
                    fleet.FleetError(f"POST -> {status}: rejected", status=status))
                self.assertEqual(journal, {})
                self.assertIn("nothing to destroy", outcome)

    def test_possibly_allocated_create_keeps_its_intent_within_the_settle_window(self):
        failures = [fleet.FleetError(f"POST -> {status}: retry", status=status) for status in (408, 409, 429, 500, 503)]
        failures += [fleet.FleetError("POST failed: connection reset"), TimeoutError("timed out")]
        for failure in failures:
            with self.subTest(failure=repr(failure), status=getattr(failure, "status", None)):
                journal, outcome = self._rejected_create_then_down(failure)
                self.assertEqual(len(journal), 1)
                (intent,) = journal.values()
                self.assertEqual(intent["engagement"], "eng")
                self.assertNotIn("id", intent)
                self.assertIn("1 unconfirmed allocations", outcome)

    def _down_with_listing(self, listing, now):
        with patch.object(fleet, "mapi", return_value=(200, listing)), \
             patch.object(fleet.time, "time", return_value=now), \
             patch.object(fleet, "destroy_machine", side_effect=lambda mc: mc["name"]) as destroy, \
             patch("sys.stdout", new_callable=__import__("io").StringIO):
            try:
                fleet.cmd_down(argparse.Namespace(force=True, out="."))
                error = None
            except fleet.FleetError as exc:
                error = exc
        return error, destroy

    def test_lost_create_intent_is_dropped_after_misses_and_settle_window(self):
        env = {"FLY_APP": "fixture-app", "HACK_ME_INTENT_SETTLE_SECONDS": "600"}
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, env, clear=True):
            intent = {"app": "fixture-app", "engagement": "eng", "name": "lost", "createdAt": 1000.0}
            fleet.save_state({"machines": [], "cleanupMachines": {"lost": intent}})
            # Many quick retries inside the window never drop it, whatever the miss count.
            for now in (1010, 1015, 1020, 1300, 1599):
                error, _ = self._down_with_listing([], now)
                self.assertIn("1 unconfirmed allocations", str(error))
            self.assertEqual(fleet.load_state()["cleanupMachines"]["lost"]["misses"], 5)
            error, destroy = self._down_with_listing([], 1601)
            self.assertIsNone(error)
            destroy.assert_not_called()
            self.assertEqual(fleet.load_state()["cleanupMachines"], {})

    def test_intent_needs_several_misses_even_after_the_window(self):
        env = {"FLY_APP": "fixture-app"}
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, env, clear=True):
            fleet.save_state({"machines": [], "cleanupMachines": {
                "old": {"app": "fixture-app", "engagement": "eng", "name": "old", "createdAt": 0.0}}})
            for expected in (1, 2):
                error, _ = self._down_with_listing([], 10_000)
                self.assertIsNotNone(error)
                self.assertEqual(fleet.load_state()["cleanupMachines"]["old"]["misses"], expected)
            error, _ = self._down_with_listing([], 10_000)
            self.assertIsNone(error)

    def test_restart_mid_wait_keeps_the_miss_count_and_clock(self):
        env = {"FLY_APP": "fixture-app"}
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, env, clear=True):
            fleet.save_state({"machines": [], "cleanupMachines": {
                "lost": {"app": "fixture-app", "engagement": "eng", "name": "lost"}}})  # pre-fix intent, no createdAt
            self._down_with_listing([], 5000)
            self._down_with_listing([], 5100)
            # A new controller process reads the same journal from disk.
            journal = json.loads(Path(fleet.STATE_FILE).read_text())["cleanupMachines"]["lost"]
            self.assertEqual((journal["misses"], journal["firstMissAt"]), (2, 5000))
            error, _ = self._down_with_listing([], 5300)  # 3 misses, but only 300 s since the first
            self.assertIsNotNone(error)
            error, _ = self._down_with_listing([], 5601)
            self.assertIsNone(error)

    def test_machine_fly_created_late_is_still_found_and_destroyed(self):
        env = {"FLY_APP": "fixture-app"}
        with tempfile.TemporaryDirectory() as root, working_directory(Path(root)), patch.dict(os.environ, env, clear=True):
            fleet.save_state({"machines": [], "cleanupMachines": {
                "late": {"app": "fixture-app", "engagement": "eng", "name": "late", "createdAt": 1000.0}}})
            self._down_with_listing([], 1010)
            self._down_with_listing([], 1100)
            late = {"id": "m-late", "name": "late", "config": {"metadata": {"engagement_id": "eng"}}}
            error, destroy = self._down_with_listing([late], 1500)
            self.assertIsNone(error)
            destroy.assert_called_once_with({"name": "late", "id": "m-late"})
            self.assertEqual(fleet.load_state()["cleanupMachines"], {})

    def test_lost_post_engagement_converges_to_a_terminal_record(self):
        env = {"FLY_APP": "fixture-app", "OPENCODE_SERVER_PASSWORD": "synthetic"}
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, env, clear=True):
            worker = self._worker(root)
            worker.work.mkdir()
            with working_directory(worker.work):
                fleet.save_state({"machines": [], "cleanupMachines": {
                    "lost": {"app": "fixture-app", "engagement": worker.request.id, "name": "lost", "createdAt": 1000.0}}})
            with patch.object(fleet, "mapi", return_value=(200, [])), patch.object(fleet.time, "time", return_value=1010):
                with self.assertRaises(fleet.FleetError):
                    cleanup_engagement(root, worker.request.id)
            self.assertIn(worker.store.get(worker.request.id)["status"], ACTIVE_STATUSES)
            for now in (1100, 1200):
                with patch.object(fleet, "mapi", return_value=(200, [])), patch.object(fleet.time, "time", return_value=now):
                    with self.assertRaises(fleet.FleetError):
                        cleanup_engagement(root, worker.request.id)
            with patch.object(fleet, "mapi", return_value=(200, [])), patch.object(fleet.time, "time", return_value=1700):
                cleanup_engagement(root, worker.request.id)
            record = worker.store.get(worker.request.id)
            self.assertEqual(record["status"], "failed")
            self.assertFalse(record["cleanupPending"])
            self.assertNotIn(worker.request.id, {item["id"] for item in worker.store.active()})

    def test_completed_report_survives_a_teardown_retry(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            report = {"summary": {"confirmed": 1}, "findings": []}
            with patch.object(worker, "_execute", return_value=report), \
                 patch.object(worker, "_teardown", side_effect=RuntimeError("synthetic teardown failure")):
                worker.run()
            record = worker.store.get(worker.request.id)
            self.assertIn(record["status"], ACTIVE_STATUSES)
            self.assertTrue(record["cleanupPending"])
            self.assertNotIn("report", {k for k, v in record.items() if v is not None})
            with patch.object(worker, "_teardown", return_value=None):
                cleanup_engagement(root, worker.request.id)
            done = worker.store.get(worker.request.id)
            self.assertEqual(done["status"], "completed")
            self.assertEqual(done["report"], report)
            self.assertIsNone(done.get("pendingReport"))

    def test_failed_assessment_with_teardown_retry_still_ends_failed(self):
        with tempfile.TemporaryDirectory() as root:
            worker = self._worker(root)
            worker.work.mkdir()
            with patch.object(worker, "_execute", side_effect=EngagementError("assessment broke")), \
                 patch.object(worker, "_teardown", side_effect=RuntimeError("synthetic teardown failure")):
                worker.run()
            self.assertIsNone(worker.store.get(worker.request.id).get("pendingReport"))
            with patch.object(worker, "_teardown", return_value=None):
                cleanup_engagement(root, worker.request.id)
            self.assertEqual(worker.store.get(worker.request.id)["status"], "failed")

    def test_duplicate_launch_finishes_without_self_deadlock(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            request = EngagementRequest.from_dict(request_dict())
            finished = threading.Event()
            responses = []
            def launch_twice():
                with patch.object(manager, "_spawn", return_value=None):
                    responses.extend([manager.launch(request), manager.launch(request)])
                finished.set()
            thread = threading.Thread(target=launch_twice, daemon=True)
            thread.start()
            self.assertTrue(finished.wait(2), "duplicate launch deadlocked")
            self.assertEqual(responses[0], responses[1])

    def test_processing_same_request_starts_one_subprocess(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            request = EngagementRequest.from_dict(request_dict())
            with patch.object(manager, "_spawn", return_value=None) as spawn:
                first = manager.launch(request)
                second = manager.launch(request)
            self.assertEqual(first, second)
            self.assertEqual(spawn.call_count, 1)

    def test_new_engagement_is_bounded_by_active_fleet_capacity(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"HACK_ME_MAX_ACTIVE": "1"}):
            store = EngagementStore(root)
            manager = EngagementProcesses(store)
            first = EngagementRequest.from_dict(request_dict())
            second = EngagementRequest.from_dict(request_dict())
            with patch.object(manager, "_spawn", return_value=None) as spawn:
                manager.launch(first)
                with self.assertRaises(EngagementCapacity):
                    manager.launch(second)
            self.assertEqual(spawn.call_count, 1)


    def test_expired_subprocess_is_terminated_and_cleaned_up_without_relaunch(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            request = EngagementRequest.from_dict(request_dict())
            store.create(request)
            manager = EngagementProcesses(store)
            class StuckProcess:
                def __init__(self):
                    self.terminated = False
                def wait(self, timeout):
                    return 0 if self.terminated else (_ for _ in ()).throw(subprocess.TimeoutExpired("worker", timeout))
                def terminate(self):
                    self.terminated = True
                def kill(self):
                    raise AssertionError("graceful terminate should have been enough")
            process = StuckProcess()
            cleanup_started = threading.Event()
            def fake_spawn(_request_id, cleanup=False):
                self.assertTrue(cleanup)
                cleanup_started.set()
            with patch.object(manager, "_spawn", side_effect=fake_spawn):
                manager._track(request.id, process, timeout_seconds=0)
                self.assertTrue(cleanup_started.wait(2))
            self.assertTrue(process.terminated)
            self.assertTrue(store.cancel_requested(request.id))



if __name__ == "__main__":
    unittest.main()
