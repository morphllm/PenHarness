"""Source-bound, per-engagement bridge from the isolated coordinator to canonical fleet.py.

The agent gets no Fly token. This listener accepts only a fixed set of fleet commands and executes
each in a short trusted subprocess. Model admission and fleet cleanup stay in their existing paths.
"""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import fleet
from .broker import COORDINATOR_MODEL_ID, INFERENCE_STOP_FILE, WORKER_MODEL_ID
from .models import EngagementError
from .store import EngagementStore

ALLOWED_COMMANDS = frozenset({"list", "status", "wait", "dispatch", "lane", "steer", "harvest"})
MAX_BODY = 4 * 1024 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def execute(state_root: str, request_id: str, request: dict) -> None:
    """Run one validated canonical fleet operation. Called only by EngagementControl."""
    store = EngagementStore(state_root)
    command = request.get("command")
    if command not in ALLOWED_COMMANDS:
        raise EngagementError("operation is not available to the paid coordinator")
    work = store._dir(request_id) / "work"
    if not work.is_dir():
        raise EngagementError("engagement workspace is unavailable")
    password = (store._dir(request_id) / ".opencode-password").read_text()
    os.environ["OPENCODE_SERVER_PASSWORD"] = password
    os.environ["WORKER_MODEL"] = f"morph-abliterated/{WORKER_MODEL_ID}"
    os.environ["LANE_MODEL"] = f"morph/{COORDINATOR_MODEL_ID}"
    os.environ.pop("HACK_ME_CONTROL_URL", None)
    result_path = work / ".control-result.json"
    result_path.unlink(missing_ok=True)
    lock = (work / ".control.lock").open("a+b")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    previous = Path.cwd()
    os.chdir(work)
    try:
        if command in ("list", "status"):
            args = argparse.Namespace()
        elif command == "wait":
            timeout = request.get("timeout", 60)
            if type(timeout) is not int:
                raise EngagementError("wait timeout must be an integer")
            args = argparse.Namespace(timeout=max(1, min(timeout, 60)))
        elif command == "dispatch":
            tasks = request.get("tasks")
            if not isinstance(tasks, str) or not tasks.strip() or len(tasks.encode()) > MAX_BODY:
                raise EngagementError("dispatch requires a non-empty bounded task file")
            stream = request.get("stream") or "dynamic"
            if not isinstance(stream, str) or not _NAME.fullmatch(stream):
                raise EngagementError("invalid dispatch stream")
            path = work / "tasks" / f"remote-{stream}.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text(tasks)
            args = argparse.Namespace(
                file=str(path.relative_to(work)), brief="brief.md",
                model=f"morph-abliterated/{WORKER_MODEL_ID}", stream=stream,
            )
        elif command == "lane":
            name = request.get("name")
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise EngagementError("invalid lane name")
            brief_text = request.get("briefText", "")
            if not isinstance(brief_text, str) or len(brief_text.encode()) > MAX_BODY:
                raise EngagementError("invalid lane brief")
            brief = "brief.md"
            if brief_text:
                path = work / "lanes" / f"{name}-brief.md"
                path.parent.mkdir(exist_ok=True)
                path.write_text(brief_text)
                brief = str(path.relative_to(work))
            machine = request.get("machine")
            if machine is not None and (not isinstance(machine, str) or not _NAME.fullmatch(machine)):
                raise EngagementError("invalid lane machine")
            args = argparse.Namespace(
                name=name, machine=machine, brief=brief, model=f"morph/{COORDINATOR_MODEL_ID}",
            )
        elif command == "steer":
            lane, message = request.get("lane"), request.get("message")
            if not isinstance(lane, str) or not _NAME.fullmatch(lane):
                raise EngagementError("invalid lane name")
            if not isinstance(message, str) or not message or len(message) > 65_536:
                raise EngagementError("invalid lane message")
            args = argparse.Namespace(lane=lane, message=message)
        else:
            args = argparse.Namespace(out=".")
        getattr(fleet, f"cmd_{command}")(args)
        if command == "harvest":
            files = {}
            for name in ("findings.d/index.jsonl", "findings.md", "dead-ends.md"):
                path = work / name
                if path.is_file():
                    files[name] = path.read_text()
            lanes = work / "lanes"
            if lanes.is_dir():
                for path in lanes.iterdir():
                    if path.is_file() and path.suffix in (".jsonl", ".md"):
                        files[f"lanes/{path.name}"] = path.read_text()
            result_path.write_text(json.dumps({"files": files}))
    finally:
        os.chdir(previous)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


class EngagementControl:
    def __init__(self, store: EngagementStore, model_broker):
        self.store = store
        self.model_broker = model_broker

    def _authorized(self, request_id: str, authorization: str, client_ip: str) -> bool:
        if not self.model_broker._authorized(request_id, authorization, client_ip):
            return False
        try:
            record = self.store.get(request_id)
            expected = record.get("controlIps", [record.get("coordinatorIp", "")])
            if not isinstance(expected, list):
                raise ValueError("invalid control address list")
            client_address = ipaddress.ip_address(client_ip)
            return client_address in {ipaddress.ip_address(value) for value in expected}
        except (KeyError, ValueError):
            return False

    def handle(self, request_id: str, handler) -> None:
        if handler.path != f"/v1/engagements/{request_id}/control":
            handler.send_error(404)
            return
        authorization = handler.headers.get("Authorization", "")
        if not self._authorized(request_id, authorization, handler.client_address[0]):
            self.model_broker.reply(handler, 401, {"error": {"message": "Unauthorized"}})
            return
        if (self.store._dir(request_id) / INFERENCE_STOP_FILE).exists():
            self.model_broker.reply(handler, 402, {"error": {"message": "Inference budget exhausted"}})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY or handler.headers.get_content_type() != "application/json":
                raise ValueError("invalid request")
            request = json.loads(handler.rfile.read(length))
            if not isinstance(request, dict) or request.get("command") not in ALLOWED_COMMANDS:
                raise ValueError("operation is not available")
        except (ValueError, json.JSONDecodeError) as exc:
            self.model_broker.reply(handler, 400, {"error": {"message": str(exc)}})
            return
        try:
            result = subprocess.run(
                [sys.executable, "-m", "engagement.control", self.store.root, request_id],
                input=json.dumps(request), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150, check=False,
                cwd=Path(__file__).resolve().parent.parent,
            )
            output = result.stdout[-512_000:]
            response = {"output": output, "ok": result.returncode == 0}
            result_path = self.store._dir(request_id) / "work" / ".control-result.json"
            if result.returncode == 0 and result_path.is_file():
                response["files"] = json.loads(result_path.read_text())["files"]
                result_path.unlink()
            self.model_broker.reply(
                handler, 200 if result.returncode == 0 else 409, response,
            )
        except subprocess.TimeoutExpired:
            self.model_broker.reply(handler, 504, {"error": {"message": "Fleet operation timed out"}})


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        execute(sys.argv[1], sys.argv[2], json.load(sys.stdin))
    except BaseException as exc:
        # Fleet's normal errors are safe operational text; no environment or credential is printed.
        print(f"fleet: error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
