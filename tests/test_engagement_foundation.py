from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from engagement.auth import AuthenticationError, VercelOidcVerifier, expected_subject
from engagement.models import AGREEMENT_VERSION, EngagementRequest, EngagementError, make_activity, public_status
from engagement.scope import (
    canonical_hostname,
    public_addresses,
    require_launch_configuration,
    require_model_broker_origin,
    validate_request_scope,
    verify_ownership,
)
from engagement.store import EngagementStore, StoreConflict


# A synthetic run key in Landing's shape (`sk-` + nanoid(48)); never a real credential.
RUN_KEY = "sk-" + "SyntheticRunKeyForTestsOnly_0123456789abcdefghijk"[:48]


def request_dict(request_id=None, hostname="app.example.com", inference_key=None):
    request_id = request_id or str(uuid.uuid4())
    body = {
        "id": request_id,
        "target": {"hostname": hostname},
        "authorization": {
            "acceptedAt": datetime.now(timezone.utc).isoformat(),
            "contactEmail": "security@example.com",
            "agreementVersion": AGREEMENT_VERSION,
        },
    }
    if inference_key is not None:
        body["inference"] = {"apiKey": inference_key}
    return body


class Answer:
    def __init__(self, value):
        self.strings = [value.encode()]


class ModelTests(unittest.TestCase):
    def test_round_trip_and_public_projection(self):
        request = EngagementRequest.from_dict(request_dict())
        self.assertEqual(EngagementRequest.from_dict(request.as_dict()), request)
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            accepted = store.create(request)
            self.assertEqual(set(accepted), {"id", "status", "detail"})
            self.assertNotIn("request", accepted)
            self.assertNotIn("pid", accepted)

    def test_rejects_an_unsupported_agreement(self):
        body = request_dict()
        body["authorization"]["agreementVersion"] = "older"
        with self.assertRaises(EngagementError):
            EngagementRequest.from_dict(body)

    def test_rejects_extra_fields_instead_of_forwarding_them(self):
        body = request_dict()
        body["report"] = {"findings": []}
        with self.assertRaises(EngagementError):
            EngagementRequest.from_dict(body)

    def test_a_restart_names_the_paid_engagement_its_dns_record_proves(self):
        paid = str(uuid.uuid4())
        body = request_dict()
        body["target"]["verificationId"] = paid
        restart = EngagementRequest.from_dict(body)
        self.assertEqual(restart.verification_id, paid)
        self.assertEqual(restart.as_dict()["target"], {"hostname": "app.example.com", "verificationId": paid})
        self.assertEqual(EngagementRequest.from_dict(restart.as_dict()), restart)
        # A paid run's request is unchanged: no verificationId in its durable form.
        self.assertEqual(EngagementRequest.from_dict(request_dict()).as_dict()["target"], {"hostname": "app.example.com"})
        for invalid in ("not-a-uuid", body["id"], 7, None):
            bad = request_dict(body["id"])
            bad["target"]["verificationId"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(EngagementError):
                EngagementRequest.from_dict(bad)
        extra = request_dict()
        extra["target"]["port"] = 443
        with self.assertRaises(EngagementError):
            EngagementRequest.from_dict(extra)


class ScopeTests(unittest.TestCase):
    def test_canonical_hostname(self):
        self.assertEqual(canonical_hostname("app.example.com"), "app.example.com")
        self.assertEqual(canonical_hostname("bücher.example"), "xn--bcher-kva.example")
        for target in (
            "https://app.example.com", "127.0.0.1", "[::1]", "localhost",
            "metadata.google.internal", "app.internal", "*.example.com",
            "app.example.com:443", "app.example.com/path", "APP.example.com.",
            "app..example.com", "-bad.example.com", "example",
        ):
            with self.subTest(target=target), self.assertRaises(EngagementError):
                canonical_hostname(target)

    def test_launch_requires_exact_hostname_and_engagement_txt(self):
        request = EngagementRequest.from_dict(request_dict())
        def resolve(name, kind, **options):
            self.assertEqual((name, kind), (f"_morph-hack-me.{request.hostname}.", "TXT"))
            return [Answer("morph-hack-me=" + request.id)]
        verify_ownership(request, resolve)
        for answers in ([], [Answer("morph-hack-me=another-engagement")]):
            with self.assertRaises(EngagementError):
                verify_ownership(request, lambda *args, **kwargs: answers)

    def test_a_restart_proves_control_with_the_paid_engagements_record(self):
        paid = str(uuid.uuid4())
        body = request_dict()
        body["target"]["verificationId"] = paid
        restart = EngagementRequest.from_dict(body)
        verify_ownership(restart, lambda *args, **kwargs: [Answer("morph-hack-me=" + paid)])
        # The restart's own id is not what the customer published.
        with self.assertRaises(EngagementError):
            verify_ownership(restart, lambda *args, **kwargs: [Answer("morph-hack-me=" + restart.id)])

    def test_all_resolved_addresses_must_be_public(self):
        self.assertEqual(public_addresses("example.com", resolver=lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]), {"93.184.216.34"})
        with self.assertRaises(EngagementError):
            public_addresses("example.com", resolver=lambda *a, **k: [
                (2, 1, 6, "", ("93.184.216.34", 443)),
                (2, 1, 6, "", ("10.0.0.4", 443)),
            ])

    def test_launch_fails_closed_without_egress_enforcement(self):
        with self.assertRaises(EngagementError):
            require_launch_configuration({})
        env = {
            "HACK_ME_LAUNCH_ENABLED": "1",
            "HACK_ME_EGRESS_ENFORCED": "1",
            "HACK_ME_EGRESS_PROXY_URL": "http://10.0.0.8:3128",
        }
        self.assertEqual(require_launch_configuration(env), env["HACK_ME_EGRESS_PROXY_URL"])
        env["HACK_ME_EGRESS_PROXY_URL"] = "http://user:password@10.0.0.8:3128"
        with self.assertRaises(EngagementError):
            require_launch_configuration(env)

    def test_private_broker_origin_requires_the_private_endpoint(self):
        origin = "http://morph-penharness-control-preflight.internal:8081"
        self.assertEqual(require_model_broker_origin(origin), origin)
        for invalid in (
            "http://morph-penharness-control-preflight.internal:8080",
            "https://morph-penharness-control-preflight.internal:8081",
            "http://public.example.com:8081",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(EngagementError):
                require_model_broker_origin(invalid)

    def test_activity_is_public_and_counters_are_validated(self):
        activity = make_activity("cves", 26, 7, cve_probes_completed=7, cves_confirmed=1)
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            request = EngagementRequest.from_dict(request_dict())
            store.create(request)
            store.update(request.id, status="running", activity=activity)
            self.assertEqual(store.get(request.id)["activity"], activity)
            self.assertEqual(public_status(store.get(request.id))["activity"], activity)
            with self.assertRaises(EngagementError):
                store.update(request.id, activity=make_activity("cves", 1, 2))



class StoreTests(unittest.TestCase):
    def test_unknown_reads_do_not_create_state(self):
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            unknown = str(uuid.uuid4())
            before = list(Path(root).iterdir())
            with self.assertRaises(KeyError):
                store.get(unknown)
            self.assertEqual(list(Path(root).iterdir()), before)
            self.assertFalse((Path(root) / unknown).exists())

    def test_idempotency_conflict_and_terminal_state(self):
        request = EngagementRequest.from_dict(request_dict())
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            store.create(request)
            self.assertEqual(store.create(request)["status"], "queued")
            other = EngagementRequest.from_dict(request_dict(request.id, "other.example.com"))
            with self.assertRaises(StoreConflict):
                store.create(other)
            store.update(request.id, status="verifying")
            store.update(request.id, status="completed", report={"findings": []})
            with self.assertRaises(StoreConflict):
                store.update(request.id, status="running")
            record = store.get(request.id)
            self.assertFalse("request" in public_status(record))

    def test_a_restart_is_a_fresh_engagement_beside_the_final_failed_one(self):
        paid = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
        body = request_dict(inference_key="sk-" + "R" * 48)
        body["target"]["verificationId"] = paid.id
        restart = EngagementRequest.from_dict(body)
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            store.create(paid)
            store.update(paid.id, status="failed", detail="Assessment failed after fleet teardown; operator review required.")
            self.assertIsNone(store.inference_key(paid.id))
            self.assertEqual(store.create(restart)["status"], "queued")
            self.assertEqual(store.inference_key(restart.id), "sk-" + "R" * 48)
            self.assertEqual(store.get(paid.id)["status"], "failed")
            with self.assertRaises(StoreConflict):
                store.update(paid.id, status="running")
            # The restart id is bound to the paid engagement it names.
            other = request_dict(restart.id)
            other["target"]["verificationId"] = str(uuid.uuid4())
            with self.assertRaises(StoreConflict):
                store.create(EngagementRequest.from_dict(other))

    def test_cancel_is_persistent_and_status_is_not_terminal_until_teardown(self):
        request = EngagementRequest.from_dict(request_dict())
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            store.create(request)
            store.request_cancel(request.id)
            self.assertTrue(store.cancel_requested(request.id))
            self.assertEqual(store.get(request.id)["status"], "queued")


class AuthTests(unittest.TestCase):
    def test_subject_is_pinned_to_project_and_production(self):
        self.assertEqual(
            expected_subject("morph", "landing", "production"),
            "owner:morph:project:landing:environment:production",
        )
        with self.assertRaises(AuthenticationError):
            expected_subject("morph", "landing", "preview")


    def test_oidc_issuer_hostname_must_be_exact(self):
        with self.assertRaises(AuthenticationError):
            VercelOidcVerifier("https://oidc.vercel.com.evil.example/team", "aud", "sub")

    def test_missing_configuration_fails_closed(self):
        with self.assertRaises(AuthenticationError):
            VercelOidcVerifier.from_environment({})


if __name__ == "__main__":
    unittest.main()
