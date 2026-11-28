"""Hashed broker credentials with hard expiry and exact source-IP binding."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import time

MAX_TTL_SECONDS = 21_600  # 6 hours, also the engagement's hard runtime ceiling.
BROKER_WORKER_PLACEHOLDER = "engagement-worker"  # Public marker, never a credential.


def credential_path(directory: Path) -> Path:
    return directory / ".broker-credential.json"


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(value, output, separators=(",", ":"), sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def issue(directory: Path, token: str, ttl_seconds: int, allowed_ips=(), now=time.time) -> dict:
    if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise ValueError("broker TTL must be between 1 second and 6 hours")
    issued = now()
    record = {
        "version": 1,
        "expiresAtUnix": issued + ttl_seconds,
        "tokenSha256": hashlib.sha256(token.encode()).hexdigest(),
        "allowedIps": sorted({ipaddress.ip_address(value).compressed for value in allowed_ips}),
    }
    _write(credential_path(directory), record)
    return record


def allow_workers(directory: Path, worker_ips, now=time.time) -> list[str]:
    """Replace access with exactly the current workers' private addresses; empty denies all."""
    path = credential_path(directory)
    record = json.loads(path.read_text())
    record["allowedIps"] = sorted({ipaddress.ip_address(value).compressed for value in worker_ips})
    _write(path, record)
    return record["allowedIps"]


def authorize(directory: Path, supplied_token: str, client_ip: str, now=time.time) -> bool:
    """No X-Forwarded-For: every call is checked against time, direct peer, and token hash."""
    try:
        record = json.loads(credential_path(directory).read_text())
        expires = record["expiresAtUnix"]
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or now() >= expires:
            return False
        peer = ipaddress.ip_address(client_ip).compressed
        if peer not in record["allowedIps"]:
            return False
        if supplied_token == BROKER_WORKER_PLACEHOLDER:
            return False
        supplied_hash = hashlib.sha256(supplied_token.encode()).hexdigest()
        return hmac.compare_digest(supplied_hash, record["tokenSha256"])
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return False


def revoke(directory: Path) -> None:
    try:
        credential_path(directory).unlink()
    except FileNotFoundError:
        pass
