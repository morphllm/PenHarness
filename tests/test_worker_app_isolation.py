"""Paid workers launch only into a dedicated worker app with no Fly secrets (fleet.cmd_up)."""

import argparse
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fleet


def _args():
    return argparse.Namespace(count=1, backend="fly", type=None, ttl=3600, from_snapshot=None,
                              image="registry.fly.io/img:1", region="iad", cpus=2, memory=2048,
                              prefix="hack-me", role="worker", skip_health=True, engagement_id="eng")


PAID = {"FLY_APP": "worker-app", "FLY_API_TOKEN": "synthetic", "OPENCODE_SERVER_PASSWORD": "synthetic",
        "HACK_ME_PAID_ENGAGEMENT": "1", "HACK_ME_CONTROL_FLY_APP": "controller-app"}


class WorkerAppIsolationTest(unittest.TestCase):
    def _up(self, env, mapi):
        with tempfile.TemporaryDirectory() as root:
            previous = os.getcwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, env, clear=True), patch.object(fleet, "mapi", side_effect=mapi) as call:
                    try:
                        fleet.cmd_up(_args())
                        error = None
                    except fleet.FleetError as exc:
                        error = exc
                    return error, [c.args[:2] for c in call.call_args_list], fleet.load_state()
            finally:
                os.chdir(previous)

    def test_refuses_the_controller_app_named_by_config_or_by_fly(self):
        for env in ({**PAID, "FLY_APP": "controller-app"},
                    {**PAID, "FLY_APP": "self-app", "FLY_APP_NAME": "self-app"}):
            with self.subTest(app=env["FLY_APP"]):
                error, calls, state = self._up(env, mapi=AssertionError("no API call expected"))
                self.assertIn("controller's own app", str(error))
                self.assertEqual(calls, [])
                self.assertEqual(state.get("cleanupMachines", {}), {})

    def test_refuses_an_app_that_has_any_secret(self):
        listing = (200, {"secrets": [{"name": "SECTEST_CANARY", "digest": "x"}]})
        error, calls, _ = self._up(PAID, mapi=[listing])
        self.assertIn("SECTEST_CANARY", str(error))
        self.assertEqual(calls, [("GET", "/apps/worker-app/secrets")])

    def test_fails_closed_when_the_secret_list_cannot_be_read(self):
        for failure in (fleet.FleetError("GET -> 401: unauthorized", status=401), TimeoutError("timed out"),
                        (200, {"unexpected": True}), (200, {"secrets": "none"}), (200, None)):
            with self.subTest(failure=repr(failure)):
                error, calls, _ = self._up(PAID, mapi=[failure])
                self.assertIn("cannot list", str(error))
                self.assertEqual(calls, [("GET", "/apps/worker-app/secrets")])

    def test_launches_when_the_worker_app_has_no_secrets(self):
        created = (200, {"id": "m1", "name": "hack-me-x", "private_ip": "fdaa::9"})
        started = (200, {"state": "started", "private_ip": "fdaa::9"})
        env = {**PAID, "BROWSER_USE_API_KEY": "synthetic-third-party"}
        error, calls, state = self._up(env, mapi=[(200, {"secrets": []}), created, started])
        self.assertIsNone(error)
        self.assertEqual(calls[:2], [("GET", "/apps/worker-app/secrets"), ("POST", "/apps/worker-app/machines")])
        self.assertEqual([m["id"] for m in state["machines"]], ["m1"])

    def test_paid_workers_never_receive_a_third_party_key(self):
        bodies = []

        def mapi(method, path, body=None, timeout=120):
            if method == "GET" and path.endswith("/secrets"):
                return 200, {"secrets": []}
            if method == "POST":
                bodies.append(body)
                return 200, {"id": "m1", "name": "n", "private_ip": "fdaa::9"}
            return 200, {"state": "started", "private_ip": "fdaa::9"}

        error, _, _ = self._up({**PAID, "BROWSER_USE_API_KEY": "synthetic-third-party"}, mapi=mapi)
        self.assertIsNone(error)
        self.assertNotIn("BROWSER_USE_API_KEY", bodies[0]["config"]["env"])

    def test_operator_hunts_are_not_subject_to_the_paid_check(self):
        env = {k: v for k, v in PAID.items() if k != "HACK_ME_PAID_ENGAGEMENT"}
        created = (200, {"id": "m1", "name": "n", "private_ip": "fdaa::9"})
        error, calls, _ = self._up(env, mapi=[created, (200, {"state": "started", "private_ip": "fdaa::9"})])
        self.assertIsNone(error)
        self.assertEqual(calls[0], ("POST", "/apps/worker-app/machines"))


class RequestErrorWrappingTest(unittest.TestCase):
    """A raw socket timeout must surface as FleetError so health-check retry loops keep retrying."""

    def test_read_timeout_and_reset_become_fleet_errors(self):
        for raised in (TimeoutError("timed out"), ConnectionResetError("reset")):
            with patch.object(fleet.urllib.request, "urlopen", side_effect=raised):
                with self.assertRaises(fleet.FleetError):
                    fleet._request("http://[fdaa::1]:4096/global/health")
            with patch.object(fleet.urllib.request.OpenerDirector, "open", side_effect=raised):
                with self.assertRaises(fleet.FleetError):
                    fleet._request("http://[fdaa::1]:4096/global/health", direct=True)

    def test_proxied_and_direct_requests_both_open(self):
        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'
        with patch.object(fleet.urllib.request, "urlopen", return_value=Resp()):
            self.assertEqual(fleet._request("https://example.invalid/x"), (200, {"ok": True}))
        with patch.object(fleet.urllib.request.OpenerDirector, "open", return_value=Resp()):
            self.assertEqual(fleet._request("http://[fdaa::1]:4096/x", direct=True), (200, {"ok": True}))

    def test_a_paid_machine_that_fails_health_is_destroyed_at_once(self):
        calls = []

        def mapi(method, path, body=None, timeout=120):
            calls.append((method, path))
            if path.endswith("/secrets"):
                return 200, {"secrets": []}
            if method == "POST":
                return 200, {"id": "m1", "name": "hack-me-x", "private_ip": "fdaa::9"}
            if method == "DELETE":
                return 200, None
            return 200, {"state": "started", "private_ip": "fdaa::9"}

        args = _args()
        args.skip_health = False
        with tempfile.TemporaryDirectory() as root:
            previous = os.getcwd()
            os.chdir(root)
            try:
                with patch.dict(os.environ, PAID, clear=True), patch.object(fleet, "mapi", side_effect=mapi), \
                     patch.object(fleet, "box_req", side_effect=RuntimeError("opencode never came up")):
                    fleet.cmd_up(args)
                    state = fleet.load_state()
            finally:
                os.chdir(previous)
        self.assertIn(("DELETE", "/apps/worker-app/machines/m1?force=true"), calls)
        self.assertEqual(state["machines"], [])
        self.assertEqual([i["id"] for i in state["cleanupMachines"].values()], ["m1"])  # teardown owns it


if __name__ == "__main__":
    unittest.main()
