import base64
import json
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from engagement.broker import COORDINATOR_MODEL_ID, INFERENCE_STOP_FILE, WORKER_MODEL_ID
from engagement.control import EngagementControl
from engagement.models import EngagementRequest
from engagement.store import EngagementStore
from engagement.worker import (
    _coordinator_prompt,
    _write_hunt,
    assessment_hostnames,
    hunt_stop_reason,
    native_session_items,
    verify_native_session,
    verify_worker_result,
)
from tests.test_engagement_foundation import RUN_KEY, request_dict


class PaidHarnessTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EngagementStore(self.temp.name)
        self.request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        self.store.create(self.request)
        self.store.update(self.request.id, status="running")
        self.work = self.store._dir(self.request.id) / "work"
        _write_hunt(self.work, self.request)

    def test_paid_start_auto_satisfies_every_setup_question(self):
        prompt = _coordinator_prompt(self.request, (self.work / "brief.md").read_text())
        self.assertIn("KYC/privileged access", prompt)
        self.assertIn("DNS proof", prompt)
        self.assertIn("fleet creation and the headless", prompt)
        self.assertIn("Do not run setup, fleet up/down, ask questions, or seek approval", prompt)
        for stage in ("pin the actually deployed stack and exact versions", "CVE reachability",
                      "logical authorization/business bugs", "chain composition",
                      "Serialize every new primitive", "create a chains lane"):
            self.assertIn(stage, prompt)
        self.assertNotIn("Complete several useful loop iterations", prompt)
        self.assertTrue((self.work / "scope.md").is_file())
        self.assertIn("Landing confirmed your account's assessment access", (self.work / "brief.md").read_text())
        self.assertEqual((self.work / "lanes.txt").read_text().splitlines(), ["surface", "stack", "cves", "logic", "chains"])
        scope = (self.work / "scope.md").read_text()
        self.assertNotIn("www.app.example.com", scope)
        self.assertIn("other hostname", scope)
        self.assertEqual(assessment_hostnames("morphllm.com"), ("morphllm.com", "www.morphllm.com"))
        self.assertEqual(assessment_hostnames("www.morphllm.com"), ("www.morphllm.com",))
        self.assertEqual(assessment_hostnames("api.morphllm.com"), ("api.morphllm.com",))
        permissions = json.loads(Path("fly/opencode.json").read_text())["permission"]
        self.assertEqual(permissions["question"], "deny")
        self.assertEqual(permissions["plan_enter"], "deny")
        for filename in ("fly/opencode.json", "opencode.json"):
            with self.subTest(config=filename):
                config = json.loads(Path(filename).read_text())
                self.assertEqual(config["subagent_depth"], 2)
                agents = config["agent"]
                self.assertEqual(agents["kimi-lane"]["model"], "morph/morph-kimik3")
                self.assertEqual(agents["kimi-lane"]["permission"]["task"],
                                 {"deepseek-worker": "allow"})
                self.assertEqual(agents["deepseek-worker"]["model"],
                                 "morph-abliterated/morph-dsv41flash-abliterated")
                self.assertEqual(agents["deepseek-worker"]["permission"]["task"], "deny")
        for agent_name in ("kimi-lane", "deepseek-worker"):
            self.assertIn(agent_name, (Path("fly/entrypoint.sh").read_text()))
            self.assertIn(agent_name, prompt)
        self.assertIn("Do not use generic agents with an inherited model", prompt)

    def test_loop_stops_on_deadline_budget_cancel_and_completion(self):
        self.assertEqual(hunt_stop_reason(self.store, self.request.id, time.monotonic() - 1, self.work), "deadline")
        deadline = time.monotonic() + 60
        self.assertIsNone(hunt_stop_reason(self.store, self.request.id, deadline, self.work))
        (self.store._dir(self.request.id) / INFERENCE_STOP_FILE).write_text("budget-exhausted\n")
        self.assertEqual(hunt_stop_reason(self.store, self.request.id, deadline, self.work), "budget")
        (self.store._dir(self.request.id) / INFERENCE_STOP_FILE).unlink()
        self.store.request_cancel(self.request.id)
        self.assertEqual(hunt_stop_reason(self.store, self.request.id, deadline, self.work), "cancelled")
        (self.store._dir(self.request.id) / "cancel-requested").unlink()
        with patch("engagement.worker.fleet._box_get_path", return_value={"content": '{"reason":"saturated"}'}):
            self.assertEqual(hunt_stop_reason(self.store, self.request.id, deadline, self.work, Mock()), "completed")

    def test_harvest_returns_evidence_and_lane_proposals_to_the_coordinator(self):
        from engagement.control import execute
        password = self.store._dir(self.request.id) / ".opencode-password"
        password.write_text("synthetic-password")
        finding = {"id": "HM-1", "status": "confirmed"}

        def harvested(_args):
            (self.work / "findings.d" / "index.jsonl").write_text(json.dumps(finding) + "\n")
            (self.work / "dead-ends.md").write_text("tested and falsified\n")
            (self.work / "lanes").mkdir(exist_ok=True)
            (self.work / "lanes" / "worker-logic-proposals.jsonl").write_text('{"task":"probe parity"}\n')

        with patch("engagement.control.fleet.cmd_harvest", side_effect=harvested):
            execute(self.store.root, self.request.id, {"command": "harvest"})
        result = json.loads((self.work / ".control-result.json").read_text())
        destination = Path(self.temp.name) / "coordinator"
        destination.mkdir()
        previous = Path.cwd()
        import os
        os.chdir(destination)
        try:
            from argparse import Namespace
            with patch.dict(os.environ, {"HACK_ME_CONTROL_URL": "http://fixture.internal:8081/control", "HACK_ME_BROKER_TOKEN": "synthetic"}), \
                 patch("fleet._request", return_value=(200, {"files": result["files"], "output": "ok"})):
                from fleet import run_paid_remote
                run_paid_remote(Namespace(command="harvest", out=None))
        finally:
            os.chdir(previous)
        self.assertEqual(json.loads((destination / "findings.d" / "index.jsonl").read_text()), finding)
        self.assertIn("probe parity", (destination / "lanes" / "worker-logic-proposals.jsonl").read_text())

    def test_default_lane_uses_the_paid_scope_brief(self):
        from engagement.control import execute
        password = self.store._dir(self.request.id) / ".opencode-password"
        password.write_text("synthetic-password")
        with patch("engagement.control.fleet.cmd_lane") as lane:
            execute(self.store.root, self.request.id, {"command": "lane", "name": "logic"})
        self.assertEqual(lane.call_args.args[0].brief, "brief.md")

    def test_native_entrypoint_materializes_scope_and_brief(self):
        brief = "Synthetic brief for an offline entrypoint test.\n"
        scope = "# Synthetic scope\nThis is not a live authorization or target.\n"
        script = r'''
set -eu
printf '#!/bin/sh\nexit 0\n' > /tmp/opencode
chmod +x /tmp/opencode
cd /opt/harness
bash fly/entrypoint.sh
cmp /work/hunt/brief.md /tmp/expected-brief
cmp /work/hunt/scope.md /tmp/expected-scope
'''
        env = {
            "PATH=/tmp:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "OPENCODE_SERVER_PASSWORD=synthetic-entrypoint-password",
            "HACK_ME_LOCAL_SUBAGENTS=1",
            "HACK_ME_PAID_BRIEF_B64=" + base64.b64encode(brief.encode()).decode(),
            "HACK_ME_PAID_SCOPE_B64=" + base64.b64encode(scope.encode()).decode(),
            "HACK_ME_EXPECTED_BRIEF_B64=" + base64.b64encode(brief.encode()).decode(),
            "HACK_ME_EXPECTED_SCOPE_B64=" + base64.b64encode(scope.encode()).decode(),
        }
        script = "printenv HACK_ME_EXPECTED_BRIEF_B64 | base64 --decode > /tmp/expected-brief\n" + script
        script = "printenv HACK_ME_EXPECTED_SCOPE_B64 | base64 --decode > /tmp/expected-scope\n" + script
        command = [
            "docker", "run", "--rm", "--network", "none",
            "--mount", f"type=bind,src={Path.cwd()},dst=/opt/harness,readonly",
            "--mount", "type=tmpfs,dst=/work",
            *sum((["-e", value] for value in env), []),
            "ubuntu:22.04", "bash", "-c", script,
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_lane_children_and_probe_children_self_dispatch(self):
        machine = {"name": "lead"}
        sessions = [
            {"id": "lead", "title": "coordinator"},
            {"id": "lane", "title": "lane-cve", "parentID": "lead"},
            {"id": "probe", "title": "worker-cve", "parentID": "lane"},
            {"id": "another-root", "title": "coordinator"},
        ]
        items = native_session_items(machine, sessions, "lead")
        self.assertEqual([item["session"] for item in items], ["lane", "probe"])
        self.assertEqual(items[1]["machine"], "lead")
        finished = [{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Controlled probe response"}]}]
        with patch("engagement.worker.fleet.box_req", return_value=(200, finished)):
            self.assertTrue(verify_native_session(machine, {"session": "probe"}))
        for messages in (
            [{"info": {"role": "assistant"}, "parts": []}],
            [{"info": {"role": "assistant", "error": {"message": "failed"}}}],
            [{"info": {"role": "user"}, "parts": [{"type": "text", "text": "not result"}]}],
        ):
            with self.subTest(messages=messages), patch("engagement.worker.fleet.box_req", return_value=(200, messages)):
                self.assertFalse(verify_native_session(machine, {"session": "probe"}))

    def test_worker_completion_requires_both_serialized_probe_artifacts(self):
        machine = {"name": "worker-one"}
        item = {"taskId": "abc123", "stream": "surface"}
        detail = "findings.d/worker-one-abc123.md"
        finding = {key: "value" for key in ("id", "stream", "worker", "status", "sev", "class", "title", "where", "proof", "control", "chain", "detail")}
        finding.update(id="abc123", stream="semantic-stream-not-the-dispatch-label", status="dead",
                       detail="The control falsified the probe", worker="worker-one")

        def document(_machine, path):
            return {
                detail: {"content": "## dead probe\nstatus: dead\n"},
                "findings.d/index.jsonl": {"content": json.dumps(finding) + "\n"},
            }.get(path)

        with patch("engagement.worker.fleet._box_get_path", side_effect=document):
            self.assertTrue(verify_worker_result(machine, item))
            with patch("engagement.worker.fleet._box_get_path", return_value={"content": ""}):
                self.assertFalse(verify_worker_result(machine, item))

    def test_paid_harvest_enumerates_real_finding_documents(self):
        import argparse
        import os
        import fleet
        previous = Path.cwd()
        os.chdir(self.work)
        try:
            fleet.save_state({"machines": [{"name": "worker-one"}], "lanes": {}, "dispatched": []})
            finding = {"id": "one", "status": "dead", "detail": "descriptive control text"}
            def get_path(_machine, path):
                if path == "findings.d/index.jsonl":
                    return {"content": json.dumps(finding) + "\n"}
                if path == "findings.d/worker-one-task.md":
                    return {"content": "## Evidence with a harmless control\n"}
                return None
            def request(_machine, _method, path, **_kwargs):
                if path.startswith("/file?") and "findings.d" in path:
                    return 200, [{"name": "worker-one-task.md"}]
                if path == "/session":
                    return 200, []
                return 200, []
            with patch.dict(os.environ, {"HACK_ME_PAID_ENGAGEMENT": "1"}), \
                 patch("fleet._box_get_path", side_effect=get_path), \
                 patch("fleet.box_req", side_effect=request):
                fleet.cmd_harvest(argparse.Namespace(out="."))
            self.assertIn("harmless control", (self.work / "remote" / "worker-one" / "worker-one-task.md").read_text())
        finally:
            os.chdir(previous)

    def test_repeated_harvest_never_prefixes_lane_filename_again(self):
        import argparse
        import os
        import fleet
        previous = Path.cwd()
        os.chdir(self.work)
        try:
            fleet.save_state({"machines": [{"name": "worker-one"}], "lanes": {}, "dispatched": []})
            def get_path(_machine, path):
                if path == "findings.d/index.jsonl":
                    return {"content": ""}
                if path == "lanes/worker-one-cve-proposals.jsonl":
                    return {"content": '{"task":"probe"}\n'}
                return None
            def request(_machine, _method, path, **_kwargs):
                if path.startswith("/file?") and "lanes" in path:
                    return 200, [{"name": "worker-one-cve-proposals.jsonl"}]
                if path == "/session":
                    return 200, []
                return 200, []
            with patch.dict(os.environ, {"HACK_ME_PAID_ENGAGEMENT": "1"}), \
                 patch("fleet._box_get_path", side_effect=get_path), \
                 patch("fleet.box_req", side_effect=request):
                fleet.cmd_harvest(argparse.Namespace(out="."))
                fleet.cmd_harvest(argparse.Namespace(out="."))
            self.assertTrue((self.work / "lanes" / "worker-one-cve-proposals.jsonl").is_file())
            self.assertFalse((self.work / "lanes" / "worker-one-worker-one-cve-proposals.jsonl").exists())
        finally:
            os.chdir(previous)

    def test_paid_harvest_never_turns_unreadable_evidence_into_a_clean_report(self):
        import argparse
        import os
        import fleet
        previous = Path.cwd()
        os.chdir(self.work)
        try:
            fleet.save_state({"machines": [{"name": "worker-one"}], "lanes": {}, "dispatched": []})
            with patch.dict(os.environ, {"HACK_ME_PAID_ENGAGEMENT": "1"}), \
                 patch("fleet._box_get_path", return_value=None):
                with self.assertRaises(fleet.FleetError):
                    fleet.cmd_harvest(argparse.Namespace(out="."))
        finally:
            os.chdir(previous)

    def test_control_bridge_requires_the_exact_coordinator_address(self):
        self.store.update(self.request.id, coordinatorIp="fdaa::10", controlIps=["fdaa::10", "fdaa::20"])
        model_broker = Mock()
        model_broker._authorized.return_value = True
        control = EngagementControl(self.store, model_broker)
        self.assertTrue(control._authorized(self.request.id, "Bearer synthetic", "fdaa::10"))
        self.assertTrue(control._authorized(self.request.id, "Bearer synthetic", "fdaa::20"))
        self.assertFalse(control._authorized(self.request.id, "Bearer synthetic", "fdaa::11"))

    def test_control_commands_pin_the_only_two_models(self):
        from engagement.control import execute
        password = self.store._dir(self.request.id) / ".opencode-password"
        password.write_text("synthetic-password")
        with patch("engagement.control.fleet.cmd_dispatch") as dispatch:
            execute(self.store.root, self.request.id, {"command": "dispatch", "tasks": "one probe\n"})
            self.assertEqual(dispatch.call_args.args[0].model, f"morph-abliterated/{WORKER_MODEL_ID}")
        with patch("engagement.control.fleet.cmd_lane") as lane:
            execute(self.store.root, self.request.id, {"command": "lane", "name": "logic"})
            self.assertEqual(lane.call_args.args[0].model, f"morph/{COORDINATOR_MODEL_ID}")
        with self.assertRaises(ValueError):
            execute(self.store.root, self.request.id, {"command": "down"})
