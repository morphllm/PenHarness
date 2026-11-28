"""Strict request/response models for paid engagements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
import uuid

AGREEMENT_VERSION = "2026-11-26-v1"  # landing src/lib/hack-me.ts HACK_ME_AGREEMENT_VERSION
STATUSES = {
    "verifying",
    "queued",
    "provisioning",
    "running",
    "harvesting",
    "completed",
    "failed",
    "cancelled",
}
ACTIVE_STATUSES = {"verifying", "queued", "provisioning", "running", "harvesting"}
ACTIVITY_PHASES = {"surface", "stack", "cves", "logic", "chains", "completed"}
_ACTIVITY_KEYS = {
    "phase", "tasksPlanned", "tasksCompleted", "cveProbesCompleted", "cvesConfirmed",
    "chainsCompleted", "chainsConfirmed",
}


def make_activity(phase, tasks_planned, tasks_completed, cve_probes_completed=0,
                  cves_confirmed=0, chains_completed=0, chains_confirmed=0):
    return {
        "phase": phase,
        "tasksPlanned": tasks_planned,
        "tasksCompleted": tasks_completed,
        "cveProbesCompleted": cve_probes_completed,
        "cvesConfirmed": cves_confirmed,
        "chainsCompleted": chains_completed,
        "chainsConfirmed": chains_confirmed,
    }


def public_activity(value):
    if not isinstance(value, dict) or set(value) != _ACTIVITY_KEYS:
        raise EngagementError("engagement has malformed activity")
    if value["phase"] not in ACTIVITY_PHASES:
        raise EngagementError("engagement activity has an unknown phase")
    for key in _ACTIVITY_KEYS - {"phase"}:
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 1_000_000:
            raise EngagementError("engagement activity has an invalid counter")
    if value["tasksCompleted"] > value["tasksPlanned"]:
        raise EngagementError("engagement activity completes more tasks than it planned")
    return dict(value)


_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# A Morph API key as Landing mints it: `sk-` + nanoid(48) (URL-safe alphabet), 51 characters.
_INFERENCE_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{48}")


class EngagementError(ValueError):
    """A safe, actionable error that may be returned to the caller."""


def _required_string(value: object, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EngagementError(f"{name} must be a non-empty string of at most {limit} characters")
    return value.strip()


def engagement_id(value: object) -> str:
    try:
        parsed = uuid.UUID(_required_string(value, "id", 36))
    except (ValueError, AttributeError) as exc:
        raise EngagementError("id must be a UUID") from exc
    return str(parsed)


def inference_api_key(value: object) -> str:
    """The run's Morph API key. Errors never echo the value."""
    if not isinstance(value, str) or not _INFERENCE_API_KEY.fullmatch(value):
        raise EngagementError("inference.apiKey must be a Morph API key")
    return value


@dataclass(frozen=True)
class EngagementRequest:
    id: str
    hostname: str
    accepted_at: str
    contact_email: str
    agreement_version: str
    # The engagement id the customer's DNS record names (`morph-hack-me=<it>`), when it is not
    # this id: a customer restart of a failed paid run is a new engagement (its own record,
    # budget and key) proving control with the paid engagement's record. Part of the request.
    verification_id: str | None = None
    # The run's own Morph API key, minted by Landing per paid engagement. It is a credential,
    # not part of the request's identity: never in repr, equality, as_dict() or record.json.
    # The store keeps it in its own 0600 file, and a retried POST may rotate it.
    inference_api_key: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: object) -> "EngagementRequest":
        if not isinstance(value, dict) or set(value) not in (
            {"id", "target", "authorization"},
            {"id", "target", "authorization", "inference"},
        ):
            raise EngagementError(
                "body must contain exactly id, target, and authorization, and optionally inference"
            )
        api_key = None
        if "inference" in value:
            inference = value["inference"]
            if not isinstance(inference, dict) or set(inference) != {"apiKey"}:
                raise EngagementError("inference must contain exactly apiKey")
            api_key = inference_api_key(inference["apiKey"])
        target = value["target"]
        authorization = value["authorization"]
        if not isinstance(target, dict) or set(target) not in ({"hostname"}, {"hostname", "verificationId"}):
            raise EngagementError("target must contain exactly hostname, and optionally verificationId")
        if not isinstance(authorization, dict) or set(authorization) != {
            "acceptedAt",
            "contactEmail",
            "agreementVersion",
        }:
            raise EngagementError(
                "authorization must contain exactly acceptedAt, contactEmail, and agreementVersion"
            )

        request_id = engagement_id(value["id"])
        verification_id = None
        if "verificationId" in target:
            try:
                verification_id = engagement_id(target["verificationId"])
            except EngagementError as exc:
                raise EngagementError("target.verificationId must be a UUID") from exc
            if verification_id == request_id:
                raise EngagementError("target.verificationId is sent only when it differs from id")
        hostname = _required_string(target["hostname"], "target.hostname", 253)
        accepted_at = _required_string(authorization["acceptedAt"], "authorization.acceptedAt", 40)
        try:
            parsed_date = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise EngagementError("authorization.acceptedAt must be an ISO-8601 timestamp") from exc
        if parsed_date.tzinfo is None:
            raise EngagementError("authorization.acceptedAt must include a timezone")
        contact_email = _required_string(authorization["contactEmail"], "authorization.contactEmail", 254)
        if not _EMAIL.fullmatch(contact_email):
            raise EngagementError("authorization.contactEmail must be an email address")
        agreement_version = _required_string(
            authorization["agreementVersion"], "authorization.agreementVersion", 40
        )
        if agreement_version != AGREEMENT_VERSION:
            raise EngagementError("authorization.agreementVersion is not supported")
        return cls(
            id=request_id,
            hostname=hostname,
            accepted_at=accepted_at,
            contact_email=contact_email,
            agreement_version=agreement_version,
            verification_id=verification_id,
            inference_api_key=api_key,
        )

    def as_dict(self) -> dict:
        """The durable request, compared on a retried POST. Never includes the inference key."""
        target = {"hostname": self.hostname}
        if self.verification_id is not None:
            target["verificationId"] = self.verification_id
        return {
            "id": self.id,
            "target": target,
            "authorization": {
                "acceptedAt": self.accepted_at,
                "contactEmail": self.contact_email,
                "agreementVersion": self.agreement_version,
            },
        }


def public_status(record: dict) -> dict:
    """Project durable state onto the only fields landing may read."""
    status = record.get("status")
    if status not in STATUSES:
        raise EngagementError("engagement has an invalid status")
    result = {"id": engagement_id(record.get("id")), "status": status}
    if record.get("detail"):
        result["detail"] = _required_string(record["detail"], "detail", 500)
    if record.get("report") is not None:
        result["report"] = record["report"]
    if record.get("activity") is not None:
        result["activity"] = public_activity(record["activity"])
    return result
