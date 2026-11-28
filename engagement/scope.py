"""Target scope validation and public-address checks."""

from __future__ import annotations

import idna
import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

from .models import EngagementError, EngagementRequest

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DENIED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "instance-data",
}
_DENIED_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".home",
    ".lan",
    ".corp",
)


def canonical_hostname(value: str) -> str:
    """Return one unambiguous DNS hostname, never a URL, address, or wildcard."""
    if not isinstance(value, str) or value != value.strip() or not value:
        raise EngagementError("target.hostname must be a single canonical hostname")
    if value.endswith(".") or value.startswith(".") or ".." in value:
        raise EngagementError("target.hostname is not canonical")
    if any(char in value for char in "/@:?#[]\\") or any(ord(char) < 33 for char in value):
        raise EngagementError("target.hostname cannot contain a URL, port, path, or credentials")
    if "*" in value:
        raise EngagementError("wildcards are not supported")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise EngagementError("IP addresses are not supported; verify a hostname instead")
    try:
        ascii_host = idna.encode(value.lower()).decode("ascii")
    except idna.IDNAError as exc:
        raise EngagementError("target.hostname is not a valid internationalized hostname") from exc
    if len(ascii_host) > 253 or "." not in ascii_host:
        raise EngagementError("target.hostname must be a fully qualified public hostname")
    if ascii_host in _DENIED_HOSTNAMES or ascii_host.endswith(_DENIED_SUFFIXES):
        raise EngagementError("local and internal hostnames are not supported")
    labels = ascii_host.split(".")
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise EngagementError("target.hostname contains an invalid DNS label")
    if labels[-1].isdigit():
        raise EngagementError("target.hostname must have a public DNS suffix")
    return ascii_host


def public_addresses(hostname: str, resolver=socket.getaddrinfo) -> set[str]:
    """Resolve every address and reject the whole target if any route is non-public."""
    addresses = set()
    try:
        answers = resolver(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EngagementError("target hostname does not resolve") from exc
    for answer in answers:
        raw = answer[4][0]
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            raise EngagementError("target hostname resolves to a non-public address")
        addresses.add(address.compressed)
    if not addresses:
        raise EngagementError("target hostname does not resolve to a public address")
    return addresses


def verify_ownership(request: EngagementRequest, resolver=None) -> None:
    """Derive both sides of the DNS challenge from the immutable paid request.

    A restart names the paid engagement's id (verification_id): its record proves control.
    """
    name = f"_morph-hack-me.{request.hostname}."
    expected = f"morph-hack-me={request.verification_id or request.id}".encode("ascii")
    try:
        if resolver is None:
            from dns.resolver import resolve
            resolver = resolve
        answers = resolver(name, "TXT", lifetime=5, search=False)
        if any(b"".join(answer.strings) == expected for answer in answers):
            return
    except Exception as exc:
        raise EngagementError("Could not verify target ownership before launch.") from exc
    raise EngagementError("The engagement ownership record is missing or has changed.")


def validate_request_scope(request: EngagementRequest) -> EngagementRequest:
    canonical = canonical_hostname(request.hostname)
    if canonical != request.hostname:
        raise EngagementError("target.hostname must be lowercase canonical DNS form")
    return request


def require_launch_configuration(environ=None) -> str:
    """Paid runs fail closed until the deployment's egress guard is explicitly present."""
    env = os.environ if environ is None else environ
    if env.get("HACK_ME_LAUNCH_ENABLED") != "1":
        raise EngagementError("automated launch is disabled pending platform preflight")
    if env.get("HACK_ME_EGRESS_ENFORCED") != "1":
        raise EngagementError("automated launch requires verified deny-by-default worker egress")
    proxy = env.get("HACK_ME_EGRESS_PROXY_URL", "")
    parsed = urlparse(proxy)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise EngagementError("worker egress proxy must be an unauthenticated HTTP(S) URL")
    return proxy


def require_model_broker_origin(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != "http" or not parsed.hostname or not parsed.hostname.endswith(".internal")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or parsed.port != 8081):
        raise EngagementError("model broker origin must be the control plane's private Fly internal HTTP endpoint")
    return value.rstrip("/")
