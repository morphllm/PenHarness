"""Authenticated HTTP control plane for paid PenHarness engagements."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback

from .auth import AuthenticationError, VercelOidcVerifier
from .broker import ModelBroker
from .control import EngagementControl
from .models import ACTIVE_STATUSES, EngagementError, EngagementRequest, engagement_id, public_status
from .retention import start_sweeper
from .store import EngagementStore, StoreConflict

HARNESS_ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 16_384
CLEANUP_RETRY_SECONDS = 5
CLEANUP_TIMEOUT_SECONDS = 300


class EngagementCapacity(EngagementError):
    pass


def _worker_command(store: EngagementStore, request_id: str, cleanup: bool) -> list[str]:
    command = [sys.executable, "-m", "engagement.worker", str(store.root), request_id]
    if cleanup:
        command.append("--cleanup")
    return command


def _identity(store: EngagementStore, pid, request_id: str, cleanup: bool, *, legacy=False):
    """Return an identity, False for confirmed absence, or None if inspection failed."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    if sys.platform.startswith("linux") and not legacy:
        # Kernel start ticks distinguish PID reuse. argv is NUL-separated on
        # Linux, so paths with spaces retain their argument boundaries.
        proc = Path("/proc") / str(pid)
        try:
            def started():
                return (proc / "stat").read_bytes().rsplit(b") ", 1)[1].split()[19]
            start = started()
            if not os.path.samefile(proc / "exe", sys.executable):
                return False
            argv = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if argv[1:] != [os.fsencode(arg) for arg in _worker_command(store, request_id, cleanup)[1:]]:
                return False
            if started() != start:
                return False
            return "proc:" + hashlib.sha256(b"\0".join([start, *argv])).hexdigest()
        except FileNotFoundError:
            return False
        except (OSError, IndexError):
            return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    fields = result.stdout.strip().split(maxsplit=5)
    if len(fields) != 6:
        return None
    try:
        arguments = shlex.split(fields[5])
    except ValueError:
        return None
    expected = _worker_command(store, request_id, cleanup)[1:]
    if not arguments or arguments[1:] != expected or not os.path.isabs(arguments[0]):
        return False
    if os.path.realpath(arguments[0]) != os.path.realpath(sys.executable):
        return False
    identity = "\0".join([*fields[:5], *arguments]).encode()
    return hashlib.sha256(identity).hexdigest()


class AdoptedProcess:
    """Wait-compatible handle for a live child left by a previous controller."""

    def __init__(self, manager, request_id, pid, cleanup, identity):
        self.manager = manager
        self.request_id = request_id
        self.pid = pid
        self.cleanup = cleanup
        self.identity = identity
        self.confirmed_exited = False

    def _alive(self):
        current = self.manager._identity(self.pid, self.request_id, self.cleanup)
        if current is None:
            # Inspection failure is not evidence that a live process exited.
            return True
        if current == self.identity:
            return True
        if current is False:
            self.confirmed_exited = True
        return False

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self._alive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("engagement.worker", timeout)
            time.sleep(min(2, remaining))
        return None

    def _signal(self, number):
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            fd = os.pidfd_open(self.pid)
            try:
                if self.manager._identity(self.pid, self.request_id, self.cleanup) == self.identity:
                    signal.pidfd_send_signal(fd, number)
            finally:
                os.close(fd)
        elif self.manager._identity(self.pid, self.request_id, self.cleanup) == self.identity:
            os.kill(self.pid, number)

    def terminate(self):
        self._signal(signal.SIGTERM)

    def kill(self):
        self._signal(signal.SIGKILL)


class EngagementProcesses:
    def __init__(self, store: EngagementStore):
        self.store = store
        self.processes = {}
        self.locks = {}
        self.guard = threading.Lock()
        self.retry_timers = {}

    def _id_lock(self, request_id: str) -> threading.Lock:
        with self.guard:
            return self.locks.setdefault(request_id, threading.Lock())

    def _identity(self, pid, request_id: str, cleanup: bool):
        return _identity(self.store, pid, request_id, cleanup)

    def _runtime_seconds(self) -> int:
        seconds = int(os.environ.get("HACK_ME_MAX_RUNTIME_SECONDS", "21600"))
        if not 1 <= seconds <= 21_600:
            raise EngagementError("HACK_ME_MAX_RUNTIME_SECONDS must be between 1 second and 6 hours")
        return seconds

    def _track(self, request_id: str, process, timeout_seconds=None, cleanup=False) -> None:
        deadline = None
        if cleanup:
            timeout = CLEANUP_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        else:
            try:
                record = self.store.get(request_id)
                created = datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")).timestamp()
                deadline = min(record.get("deadlineAtUnix") or float("inf"), created + self._runtime_seconds())
            except KeyError:
                deadline = None
            timeout = max(0, deadline - time.time()) if deadline else self._runtime_seconds()
            if timeout_seconds is not None:
                timeout = timeout_seconds
        grace = int(os.environ.get("HACK_ME_TEARDOWN_GRACE_SECONDS", "60"))
        with self.guard:
            self.processes[request_id] = process

        def reap():
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            if timed_out:
                try:
                    if not cleanup:
                        self.store.request_cancel(request_id)
                    # Let a worker attempt its own final harvest first. A stuck
                    # worker never gets another assessment launch.
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    try:
                        process.terminate()
                        process.wait(timeout=15)
                    except ProcessLookupError:
                        pass
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                            process.wait(timeout=15)
                        except ProcessLookupError:
                            pass
                        except subprocess.TimeoutExpired:
                            # Keep supervising until exit is confirmed. Never
                            # race teardown against an unconfirmed live child.
                            self._track(request_id, process, timeout_seconds=grace, cleanup=cleanup)
                            return
            with self.guard:
                if self.processes.get(request_id) is process:
                    self.processes.pop(request_id, None)
            if cleanup:
                try:
                    record = self.store.get(request_id)
                except KeyError:
                    return
                if record["status"] in ACTIVE_STATUSES:
                    # Any cleanup exit without a terminal record requires a
                    # delayed retry, even if the constructor died before it
                    # could persist cleanupPending.
                    if timed_out or not record.get("cleanupPending"):
                        self.store.update(
                            request_id,
                            cleanupPending=True,
                            detail="Fleet cleanup stopped before confirmation; retry is scheduled.",
                            pid=None,
                            processIdentity=None,
                        )
                    self._schedule_cleanup_retry(request_id)
                    return
            # A Popen/reaped child is OS-confirmed gone. Adopted handles carry
            # that proof explicitly so transient inspection errors cannot race
            # a live assessment with cleanup.
            self._reconcile(request_id, child_exited=getattr(process, "confirmed_exited", True))

        threading.Thread(target=reap, name=f"engagement-reap-{request_id}", daemon=True).start()

    def _spawn(self, request_id: str, cleanup: bool = False) -> None:
        if not cleanup:
            deadline = self.store.get(request_id).get("deadlineAtUnix")
            if deadline is None:
                self.store.update(request_id, deadlineAtUnix=time.time() + self._runtime_seconds())
        log_path = self.store._dir(request_id) / ("cleanup.log" if cleanup else "worker.log")
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        command = _worker_command(self.store, request_id, cleanup)
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                command, cwd=HARNESS_ROOT, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=env
            )
        finally:
            os.close(fd)
        # Register supervision before recording the PID. An already exited child
        # is still reconciled, and an uncaught bookkeeping error cannot orphan it.
        self._track(request_id, process, cleanup=cleanup)
        self.store.update(
            request_id,
            only_if_active=True,
            pid=process.pid,
            processCleanup=cleanup,
            processIdentity=self._identity(process.pid, request_id, cleanup),
        )

    def launch(self, request: EngagementRequest) -> dict:
        # Capacity admission and reservation are serialized globally, not just
        # per ID. Per-ID locks retain idempotent same-request behavior.
        reconcile = False
        with self.store.admission():
            with self._id_lock(request.id):
                try:
                    existing = self.store.get(request.id)
                except KeyError:
                    existing = None
                if existing is None:
                    maximum = int(os.environ.get("HACK_ME_MAX_ACTIVE", "16"))
                    if len(self.store.active()) >= maximum:
                        raise EngagementCapacity("the assessment worker fleet is at capacity; retry shortly")
                accepted = self.store.create(request)
                if existing is not None:
                    reconcile = existing["status"] in ACTIVE_STATUSES and (
                        bool(existing.get("cleanupPending")) or existing.get("pid") is not None
                    )
                else:
                    try:
                        self._spawn(request.id)
                    except Exception:
                        # Popen may have succeeded before identity bookkeeping
                        # failed. Only confirmed teardown may close the record.
                        self.store.update(request.id, cleanupPending=True, detail="Launch interrupted; cleanup required.")
                        self._schedule_cleanup_retry(request.id)
                        raise
        # Reconcile outside both locks: an exited active child needs cleanup,
        # while a live tracked child is left alone. This path cannot self-deadlock.
        if reconcile:
            self._reconcile(request.id)
        return accepted

    def _schedule_cleanup_retry(self, request_id: str) -> None:
        with self.guard:
            if request_id in self.retry_timers or request_id in self.processes:
                return
            timer = threading.Timer(CLEANUP_RETRY_SECONDS, self._cleanup_retry, args=(request_id,))
            timer.daemon = True
            self.retry_timers[request_id] = timer
            timer.start()

    def _cleanup_retry(self, request_id: str) -> None:
        with self.guard:
            self.retry_timers.pop(request_id, None)
        self._reconcile(request_id)

    def _reconcile(self, request_id: str, child_exited: bool = False) -> None:
        with self._id_lock(request_id):
            with self.guard:
                if request_id in self.processes:
                    return
            try:
                record = self.store.get(request_id)
            except KeyError:
                return
            if record["status"] not in ACTIVE_STATUSES:
                return
            pid = record.get("pid")
            child_exited = child_exited or not pid
            if not child_exited:
                cleanup = bool(record.get("processCleanup"))
                identity = self._identity(pid, request_id, cleanup)
                if identity is None:
                    self._schedule_cleanup_retry(request_id)
                    return
                saved = record.get("processIdentity")
                if isinstance(identity, str) and identity.startswith("proc:") and isinstance(saved, str) and not saved.startswith("proc:"):
                    # Older records used ps start time and argv. Upgrade only
                    # after verifying both representations of this invocation.
                    legacy = _identity(self.store, pid, request_id, cleanup, legacy=True)
                    if legacy is None:
                        self._schedule_cleanup_retry(request_id)
                        return
                    if legacy == saved:
                        self.store.update(request_id, processIdentity=identity)
                        saved = identity
                if isinstance(identity, str) and saved in (None, identity):
                    if record.get("processIdentity") is None:
                        # An earlier transient inspection failure is not proof
                        # of replacement. The current digest proves this exact
                        # engagement invocation before we adopt it.
                        self.store.update(request_id, processIdentity=identity)
                    self._track(
                        request_id,
                        AdoptedProcess(self, request_id, pid, cleanup, identity),
                        cleanup=cleanup,
                    )
                    return
            # All active, unowned children lead only to a cleanup worker. This
            # also revokes credentials if control died before workspace creation.
            try:
                self.store.update(request_id, cleanupPending=True)
                self._spawn(request_id, cleanup=True)
            except BaseException:
                traceback.print_exc()
                self.store.update(
                    request_id,
                    cleanupPending=True,
                    detail="Fleet cleanup could not start; retry is scheduled.",
                    pid=None,
                    processIdentity=None,
                )
                self._schedule_cleanup_retry(request_id)

    def recover(self) -> None:
        """Adopt verified live workers; reconcile every other active record."""
        for record in self.store.active():
            self._reconcile(record["id"])


def make_handler(store: EngagementStore, manager: EngagementProcesses, verifier: VercelOidcVerifier):
    """Public management listener. Model traffic is deliberately not routed here."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "MorphPenHarness/1.0"

        def _json(self, status: int, value: dict) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            try:
                verifier.verify(self.headers.get("Authorization", ""))
                return True
            except AuthenticationError:
                self._json(401, {"error": "Unauthorized"})
                return False

        def do_GET(self):
            if self.path == "/health":
                self._json(200, {"status": "ok"})
                return
            if not self._authorized():
                return
            try:
                request_id = engagement_id(self.path.removeprefix("/v1/engagements/"))
                record = store.get(request_id)
                self._json(200, public_status(record))
            except (EngagementError, ValueError):
                self._json(400, {"error": "Invalid engagement id"})
            except KeyError:
                self._json(404, {"error": "Engagement not found"})

        def do_POST(self):
            if self.path != "/v1/engagements":
                self._json(404, {"error": "Not found"})
                return
            if not self._authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY:
                    raise EngagementError("invalid request size")
                if self.headers.get_content_type() != "application/json":
                    raise EngagementError("Content-Type must be application/json")
                request = EngagementRequest.from_dict(json.loads(self.rfile.read(length)))
                result = manager.launch(request)
                self._json(202, result)
            except StoreConflict as exc:
                self._json(409, {"error": str(exc)})
            except EngagementCapacity as exc:
                self._json(429, {"error": str(exc)})
            except (EngagementError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception:
                traceback.print_exc()
                self._json(500, {"error": "Could not launch engagement"})

        def do_DELETE(self):
            if not self._authorized():
                return
            try:
                request_id = engagement_id(self.path.removeprefix("/v1/engagements/"))
                self._json(200, store.request_cancel(request_id))
            except (EngagementError, ValueError):
                self._json(400, {"error": "Invalid engagement id"})
            except KeyError:
                self._json(404, {"error": "Engagement not found"})

        def log_message(self, format, *args):
            # Request paths only; never log headers, bodies, tokens, or fleet state.
            super().log_message(format, *args)

    return Handler


def make_broker_handler(store: EngagementStore, model_broker: ModelBroker):
    """Private-worker-only listener. No public management or OIDC routes here."""
    control = EngagementControl(store, model_broker)

    class BrokerHandler(BaseHTTPRequestHandler):
        server_version = "MorphPenHarnessBroker/1.0"

        def do_POST(self):
            parts = self.path.strip("/").split("/")
            is_model = len(parts) == 5 and parts[:2] == ["v1", "engagements"] and parts[3:] == ["chat", "completions"]
            is_control = len(parts) == 4 and parts[:2] == ["v1", "engagements"] and parts[3] == "control"
            if not (is_model or is_control):
                self.send_error(404)
                return
            try:
                request_id = engagement_id(parts[2])
                if is_control:
                    control.handle(request_id, self)
                else:
                    model_broker.handle(request_id, self)
            except EngagementError:
                body = json.dumps({"error": "Invalid engagement id"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    return BrokerHandler


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    # socketserver's default listen backlog is 5. A task worker that opens dozens of model
    # streams at once overflows it, and the kernel then resets or drops the extra connects.
    request_queue_size = 128


def main() -> None:
    store = EngagementStore(os.environ.get("PENHARNESS_STATE_ROOT", "/data/engagements"))
    # One recovery supervisor per state volume. The descriptor stays open for
    # the service lifetime and the kernel releases it on process exit.
    with (store.root / ".supervisor.lock").open("a+b") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        serve(store)


def serve(store: EngagementStore) -> None:
    start_sweeper(store)
    manager = EngagementProcesses(store)
    manager.recover()
    verifier = VercelOidcVerifier.from_environment()
    model_broker = ModelBroker(store)
    address = os.environ.get("PENHARNESS_BIND", "::")
    port = int(os.environ.get("PENHARNESS_PORT", "8080"))
    broker_bind = os.environ.get("PENHARNESS_BROKER_BIND", os.environ.get("FLY_PRIVATE_IP", ""))
    broker_port = int(os.environ.get("PENHARNESS_BROKER_PORT", "8081"))
    broker_address = ipaddress.ip_address(broker_bind)
    if broker_address not in ipaddress.IPv6Network("fdaa::/16"):
        raise RuntimeError("the model broker requires its exact private Fly6PN address")
    if not 1 <= broker_port <= 65535 or broker_port == port:
        raise RuntimeError("the model broker requires a separate valid private port")

    private_server = IPv6ThreadingHTTPServer(
        (broker_address.compressed, broker_port), make_broker_handler(store, model_broker)
    )
    public_server = IPv6ThreadingHTTPServer((address, port), make_handler(store, manager, verifier))
    private_server.daemon_threads = True
    public_server.daemon_threads = True
    private_thread = threading.Thread(
        target=private_server.serve_forever, name="private-model-broker", daemon=True
    )
    private_thread.start()
    try:
        public_server.serve_forever()
    finally:
        private_server.shutdown()
        private_server.server_close()
        public_server.server_close()
        private_thread.join(timeout=5)


if __name__ == "__main__":
    main()
