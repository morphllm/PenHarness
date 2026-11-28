"""Small, durable and isolated JSON store for engagement control state."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import uuid

from .models import (
    ACTIVE_STATUSES,
    EngagementError,
    EngagementRequest,
    inference_api_key,
    public_activity,
    public_status,
)

# The run's Morph API key (Landing mints one per paid engagement). It lives only in this
# file, mode 0600, beside record.json and never inside it, so public_status, logs and the
# request comparison cannot carry it. Only the controller's broker reads it.
INFERENCE_KEY_FILE = "inference-api-key"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class StoreConflict(EngagementError):
    pass


class EngagementStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _dir(self, request_id: str) -> Path:
        # UUID validation above and here keeps every caller inside the state root.
        return self.root / str(uuid.UUID(request_id))

    def _record_path(self, request_id: str) -> Path:
        return self._dir(request_id) / "record.json"

    @contextmanager
    def _lock(self, request_id: str):
        directory = self._dir(request_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (directory / ".lock").open("a+") as lock:
            os.chmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def admission(self):
        # All controllers using this state volume count and reserve under one lock.
        with (self.root / ".admission.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump(value, output, separators=(",", ":"), sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _inference_key_path(self, request_id: str) -> Path:
        return self._dir(request_id) / INFERENCE_KEY_FILE

    def _install_inference_key_unlocked(self, request_id: str, api_key: str) -> None:
        """Atomically make `api_key` the run's key. Caller holds the engagement lock."""
        api_key = inference_api_key(api_key)
        path = self._inference_key_path(request_id)
        # O_EXCL on a fresh random name: never opens, truncates or follows anything that is
        # already there. os.replace then swaps it in whole, so a reader sees the old key or
        # the new one, never a partial file.
        temporary = path.with_name(f".{INFERENCE_KEY_FILE}.{secrets.token_hex(8)}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(api_key)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        self._fsync_directory(path.parent)

    def inference_key(self, request_id: str) -> str | None:
        """The run's Morph API key, or None when it has none (missing, revoked or malformed)."""
        try:
            fd = os.open(self._inference_key_path(request_id), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:  # missing, or a symlink planted in its place (ELOOP)
            return None
        with os.fdopen(fd) as source:
            value = source.read(128)
        try:
            return inference_api_key(value)
        except EngagementError:
            return None

    def _delete_inference_key_unlocked(self, request_id: str) -> None:
        path = self._inference_key_path(request_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self._fsync_directory(path.parent)

    def delete_inference_key(self, request_id: str) -> None:
        """Idempotent. Landing disables the key itself; this removes PenHarness's copy."""
        with self._lock(request_id):
            self._delete_inference_key_unlocked(request_id)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _accepts_inference_key(self, request_id: str, record: dict | None) -> bool:
        # A run that is stopping or finished gets no (new) key: it must not call the model.
        if self.cancel_requested(request_id):
            return False
        return record is None or (record["status"] in ACTIVE_STATUSES and not record.get("cleanupPending"))

    def create(self, request: EngagementRequest) -> dict:
        with self._lock(request.id):
            path = self._record_path(request.id)
            if path.exists():
                existing = self._read_unlocked(request.id)
                # The key is not part of the request (as_dict omits it): a retried POST that
                # carries a rotated key is the same request, and the newest key wins.
                if existing["request"] != request.as_dict():
                    raise StoreConflict("engagement id was already used for a different request")
                if request.inference_api_key and self._accepts_inference_key(request.id, existing):
                    self._install_inference_key_unlocked(request.id, request.inference_api_key)
                return public_status(existing)
            if request.inference_api_key and self._accepts_inference_key(request.id, None):
                # Before record.json, so no record of this run exists without its key.
                self._install_inference_key_unlocked(request.id, request.inference_api_key)
            now = utc_now()
            record = {
                "id": request.id,
                "request": request.as_dict(),
                "hostname": request.hostname,
                "status": "queued",
                "detail": "Paid engagement accepted; waiting for launch preflight.",
                "report": None,
                "pid": None,
                "cleanupPending": False,
                "createdAt": now,
                "updatedAt": now,
            }
            self._write(path, record)
            return public_status(record)

    def _read_unlocked(self, request_id: str) -> dict:
        try:
            with self._record_path(request_id).open() as source:
                record = json.load(source)
        except FileNotFoundError as exc:
            raise KeyError(request_id) from exc
        if record.get("id") != str(uuid.UUID(request_id)):
            raise EngagementError("engagement record is corrupt")
        return record

    def get(self, request_id: str) -> dict:
        # Updates use atomic os.replace(). Reads must not create directories or
        # lock files for unauthenticated, unknown engagement IDs.
        return self._read_unlocked(request_id)

    def update(self, request_id: str, *, only_if_active=False, **changes) -> dict:
        with self._lock(request_id):
            record = self._read_unlocked(request_id)
            if only_if_active and record["status"] not in ACTIVE_STATUSES:
                return record
            if changes.get("status") == "completed" and self.cancel_requested(request_id):
                changes.update(status="cancelled", detail="Cancelled; worker fleet teardown completed.", report=None)
            current = record["status"]
            incoming = changes.get("status", current)
            if current != incoming:
                if current not in ACTIVE_STATUSES:
                    raise StoreConflict(f"cannot move a terminal engagement from {current} to {incoming}")
                if incoming not in {
                    "verifying",
                    "provisioning",
                    "running",
                    "harvesting",
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    raise StoreConflict(f"invalid engagement status {incoming}")
            if "activity" in changes:
                changes["activity"] = public_activity(changes["activity"])
            if incoming not in ACTIVE_STATUSES:
                # A terminal status is written only once teardown is confirmed (the worker and
                # cleanup write it after the fleet is down). The run's key goes first, so no
                # terminal record ever sits beside a usable key.
                self._delete_inference_key_unlocked(request_id)
            record.update(changes)
            record["updatedAt"] = utc_now()
            self._write(self._record_path(request_id), record)
            return record

    def request_cancel(self, request_id: str) -> dict:
        """Idempotent. Raises KeyError for an unknown id, after leaving a tombstone.

        Landing may cancel while its POST for the same id is still in flight. The
        tombstone makes a record created afterwards start cancel-requested, so its
        worker stops at its first check instead of running unstoppably.
        """
        with self._lock(request_id):
            cancel_path = self._dir(request_id) / "cancel-requested"
            try:
                record = self._read_unlocked(request_id)
            except KeyError:
                cancel_path.touch(mode=0o600, exist_ok=True)
                raise
            if record["status"] in ACTIVE_STATUSES:
                cancel_path.touch(mode=0o600, exist_ok=True)
                # A repeated cancel must not hide that teardown is still unconfirmed.
                if not record.get("cleanupPending"):
                    record["detail"] = "Cancellation requested; worker fleet teardown is required."
                    record["updatedAt"] = utc_now()
                    self._write(self._record_path(request_id), record)
            return public_status(record)

    def cancel_requested(self, request_id: str) -> bool:
        return (self._dir(request_id) / "cancel-requested").exists()

    def active(self) -> list[dict]:
        records = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            try:
                request_id = str(uuid.UUID(directory.name))
                record = self.get(request_id)
            except (ValueError, KeyError):
                continue
            if record["status"] in ACTIVE_STATUSES:
                records.append(record)
        return records
