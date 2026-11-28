"""Two-model Morph broker. The run's provider key never leaves the controller.

The upstream key is the engagement's own Morph API key (one per paid run, minted by Landing and
stored by EngagementStore beside the record), never a process-wide MORPH_API_KEY.

Every upstream call is paid for out of the engagement's durable inference budget
(engagement/budget.py): the broker reserves the request's worst-case charge before forwarding,
watches the engagement while it relays, and settles once from the upstream's reported usage.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

from . import budget
from .credential import authorize as authorize_credential
from .models import ACTIVE_STATUSES
from .store import EngagementStore

COORDINATOR_MODEL_ID = budget.COORDINATOR_MODEL_ID
WORKER_MODEL_ID = budget.WORKER_MODEL_ID
MODEL_ID = WORKER_MODEL_ID  # Backwards-compatible name for the original worker-model tests.
ALLOWED_MODELS = frozenset((COORDINATOR_MODEL_ID, WORKER_MODEL_ID))
MAX_BODY = 4 * 1024 * 1024
MAX_OUTPUT_TOKENS = 32_768
DEFAULT_UPSTREAM = "https://api.morphllm.com"
INFERENCE_STOP_FILE = "inference-stop"
# How often a relay re-checks cancellation, cleanup, deadline and credential while the upstream
# is silent. Every received chunk is also checked.
WATCH_INTERVAL_SECONDS = 2.0
# Non-streaming responses are buffered up to this size to read their usage while relaying.
MAX_USAGE_BUFFER = 16 * 1024 * 1024
# Attempts per call when the upstream provably never received the request (connect, TLS or a
# partial send failed). Nothing can have been billed, so a fresh connection is safe; a failure
# after the whole request was sent is never retried here.
UPSTREAM_ATTEMPTS = 3
UPSTREAM_RETRY_BACKOFF_SECONDS = 0.25
USER_AGENT = "MorphPenHarnessBroker/1.0"
# Settlement outcomes with this prefix charge nothing: the call was never sent upstream intact.
RELEASED = "released:"
# Request fields that make the server generate or tokenize more than the body and max_tokens
# account for. Each is refused rather than priced.
_AMPLIFYING_FIELDS = ("chat_template", "mm_processor_kwargs", "best_of", "use_beam_search")


def _json_bytes(value: dict) -> bytes:
    # ensure_ascii (the default) keeps every character at least as long as its UTF-8 form,
    # which budget.input_token_upper_bound() relies on.
    return json.dumps(value, separators=(",", ":")).encode()


def _public_error(message: str, kind: str = "request_error") -> dict:
    return {"error": {"message": message, "type": kind}}


def _text_only(messages) -> bool:
    """True when every message carries only text, so body bytes bound its tokens."""
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if not isinstance(content, list):
            return False
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                return False
    return True


def validate_payload(payload) -> int:
    """Normalize a chat request in place and return its output-token ceiling."""
    if not isinstance(payload, dict) or payload.get("model") not in ALLOWED_MODELS:
        raise ValueError("only morph-kimik3 and morph-dsv41flash-abliterated are allowed")
    limits = [payload[key] for key in ("max_tokens", "max_completion_tokens") if key in payload]
    for value in limits:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_OUTPUT_TOKENS:
            raise ValueError("max_tokens exceeds the engagement limit")
    max_tokens = min(limits) if limits else MAX_OUTPUT_TOKENS
    payload["max_tokens"] = max_tokens
    if "max_completion_tokens" in payload:
        payload["max_completion_tokens"] = max_tokens
    if payload.get("n", 1) != 1 or isinstance(payload.get("n"), bool):
        raise ValueError("n must be 1")
    for field in _AMPLIFYING_FIELDS:
        if field in payload:
            raise ValueError(f"{field} is not allowed")
    if not _text_only(payload.get("messages")):
        raise ValueError("messages must contain only text content")
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        raise ValueError("stream must be a boolean")
    if stream:
        if not isinstance(payload.get("stream_options", {}), dict):
            raise ValueError("stream_options must be an object")
        # The final usage chunk is how a stream settles below its reservation.
        payload["stream_options"] = {"include_usage": True}
    else:
        payload.pop("stream_options", None)
    return max_tokens


class UsageMeter:
    """Extracts the upstream's reported usage while bytes are relayed."""

    def __init__(self, stream: bool):
        self.stream = stream
        self.pending = b""
        self.buffer = bytearray()
        self.overflow = False
        self.usages = []

    def feed(self, chunk: bytes) -> None:
        if not self.stream:
            if len(self.buffer) + len(chunk) > MAX_USAGE_BUFFER:
                self.overflow = True
                self.buffer.clear()
            elif not self.overflow:
                self.buffer += chunk
            return
        self.pending += chunk
        *lines, self.pending = self.pending.split(b"\n")
        if len(self.pending) > MAX_USAGE_BUFFER:
            self.pending = b""
        for line in lines:
            line = line.rstrip(b"\r")
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            # OpenAI's include_usage contract: one final chunk, empty choices, usage set.
            if isinstance(event, dict) and isinstance(event.get("usage"), dict) and event.get("choices") == []:
                self.usages.append(event["usage"])

    def finish(self) -> None:
        if self.stream:
            self.feed(b"\n")
            return
        if self.overflow or not self.buffer:
            return
        try:
            body = json.loads(bytes(self.buffer))
        except ValueError:
            return
        if isinstance(body, dict) and isinstance(body.get("usage"), dict):
            self.usages.append(body["usage"])


def settled_charge(usages: list, input_upper: int, max_tokens: int, price) -> int | None:
    """Actual charge from reported usage, or None when usage is missing, malformed or out of bounds."""
    if len(usages) != 1:
        return None  # None reported, or several that disagree about what was billed.
    usage = usages[0]
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if type(prompt) is not int or type(completion) is not int:
        return None
    if not 0 <= prompt <= input_upper or not 0 <= completion <= max_tokens:
        return None
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != prompt + completion):
        return None
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    if reasoning is not None and (type(reasoning) is not int or not 0 <= reasoning <= completion):
        return None
    return budget.charge_microusd(prompt, completion, price)


class UpstreamFailure(Exception):
    """An upstream call failed before response headers.

    `sent` is False only when the upstream provably never received the complete request:
    DNS, TCP or TLS failed (no request byte was written), or sendall() of the single
    headers+body write raised, so at most a strict prefix of the body reached the network and a
    strict prefix of a JSON object is never a parseable chat request. Anything else, including
    a reset while awaiting headers, may have been run and billed upstream.
    """

    def __init__(self, phase: str, cause: BaseException, elapsed: float, sent: bool):
        super().__init__(f"{phase}: {type(cause).__name__}")
        self.phase = phase
        self.cause = cause
        self.elapsed = elapsed
        self.sent = sent

    def describe(self) -> str:
        errno = getattr(self.cause, "errno", None)
        return (f"phase={self.phase} sent={'yes' if self.sent else 'no'} "
                f"error={type(self.cause).__name__} errno={errno} after={int(self.elapsed * 1000)}ms")


class _HttpUpstream:
    """A streaming upstream response that another thread may abort."""

    def __init__(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse):
        self.connection = connection
        self.response = response
        self.lock = threading.Lock()
        self.closed = False

    def read(self, amount: int) -> bytes:
        return self.response.read1(amount)

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
        sock = self.connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.connection.close()


class ModelBroker:
    def __init__(self, store: EngagementStore, transport=None, environ=os.environ):
        self.store = store
        self.transport = transport
        self._connections = {}
        self._connections_guard = threading.Lock()
        self.environ = environ
        self.watch_interval = WATCH_INTERVAL_SECONDS
        self.upstream_attempts = UPSTREAM_ATTEMPTS
        self.retry_backoff = UPSTREAM_RETRY_BACKOFF_SECONDS
        # Dollars are the only per-engagement limit; there is no request-count cap.
        self.ledger = budget.InferenceLedger(store)

    def _mark_stopped(self, request_id: str, reason: str) -> None:
        # The paid hunt monitor harvests and tears down instead of letting the idle plugin retry
        # until the wall deadline. This durable marker also survives a controller restart.
        path = self.store._dir(request_id) / INFERENCE_STOP_FILE
        if not path.exists():
            path.write_text(reason + "\n")

    def _authorized(self, request_id: str, authorization: str, client_ip: str) -> bool:
        try:
            record = self.store.get(request_id)
        except (KeyError, ValueError):
            return False
        if (record["status"] not in ACTIVE_STATUSES or record.get("cleanupPending")
                or self.store.cancel_requested(request_id) or not authorization.startswith("Bearer ")):
            return False
        deadline = record.get("deadlineAtUnix")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline) or time.time() >= deadline
        ):
            return False
        return authorize_credential(
            self.store._dir(request_id), authorization.removeprefix("Bearer "), client_ip
        )

    @staticmethod
    def _http_transport(body: bytes, api_key: str, on_connection=None) -> tuple[int, dict, object]:
        # `api_key` is the engagement's own Morph key (EngagementStore.inference_key). There is
        # deliberately no fallback to a shared MORPH_API_KEY.
        base = os.environ.get("HACK_ME_MORPH_BASE_URL", DEFAULT_UPSTREAM)
        parsed = urlparse(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("Morph upstream must be an unauthenticated HTTPS URL")
        base_path = parsed.path.rstrip("/")
        path = base_path + ("/chat/completions" if base_path.endswith("/v1") else "/v1/chat/completions")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        }
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=600)
        if on_connection is not None:
            on_connection(connection)
        started = time.monotonic()
        phase = "connect"
        try:
            # DNS, TCP and TLS. No byte of the request has been written yet.
            connection.connect()
            phase = "send"
            # http.client sends a bytes body together with the headers in one sendall(); if it
            # raises, the upstream holds at most a strict prefix of the JSON body.
            connection.request("POST", path, body=body, headers=headers)
            phase = "headers"
            response = connection.getresponse()
        except Exception as exc:
            connection.close()
            # Only a network error before the request was fully written proves it never arrived;
            # anything unexpected is treated as possibly delivered.
            delivered = phase == "headers" or not isinstance(exc, (OSError, http.client.HTTPException))
            raise UpstreamFailure(phase, exc, time.monotonic() - started, sent=delivered) from exc
        except BaseException:
            connection.close()
            raise
        return response.status, dict(response.getheaders()), _HttpUpstream(connection, response)

    def _live_transport(self, call_key: str, body: bytes, api_key: str):
        opened = []
        def register(connection):
            opened.append(connection)
            with self._connections_guard:
                self._connections.setdefault(call_key, set()).add(connection)
        try:
            return self._http_transport(body, api_key, on_connection=register)
        finally:
            with self._connections_guard:
                active = self._connections.get(call_key, set())
                active.difference_update(opened)
                if not active:
                    self._connections.pop(call_key, None)

    def _close_connections(self, call_key: str) -> None:
        with self._connections_guard:
            connections = list(self._connections.get(call_key, set()))
        for connection in connections:
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()

    def handle(self, request_id: str, handler) -> None:
        if handler.path != f"/v1/engagements/{request_id}/chat/completions":
            handler.send_error(404)
            return
        authorization = handler.headers.get("Authorization", "")
        client_ip = handler.client_address[0]
        handler.log_message('broker: verifying peer authorization')
        if not self._authorized(request_id, authorization, client_ip):
            self.reply(handler, 401, _public_error("Unauthorized", "authentication_error"))
            return
        handler.log_message('broker: reading model request')
        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY or handler.headers.get_content_type() != "application/json":
                raise ValueError("invalid request")
            payload = json.loads(handler.rfile.read(length))
            max_tokens = validate_payload(payload)
            body = _json_bytes(payload)
            if len(body) > MAX_BODY:
                raise ValueError("invalid request")
        except (ValueError, json.JSONDecodeError) as exc:
            self.reply(handler, 400, _public_error(str(exc)))
            return
        try:
            price = budget.prices(self.environ, payload["model"])
        except budget.PricingError as exc:
            handler.log_message('broker: refusing paid call, %s', exc)
            self.reply(handler, 503, _public_error("Model pricing is misconfigured", "budget_unavailable"))
            return
        # The engagement's own Morph key, which Landing limits to this run ($1,000, 3 days)
        # and disables when it ends. Without one the call is refused before anything is
        # reserved: a paid engagement never falls back to a shared provider key.
        api_key = self.store.inference_key(request_id)
        if api_key is None:
            handler.log_message('broker: refusing paid call, engagement has no inference key')
            self.reply(handler, 503, _public_error("Inference credential unavailable", "credential_unavailable"))
            return
        input_upper = budget.input_token_upper_bound(body)
        maximum = budget.charge_microusd(input_upper, max_tokens, price)
        call_id = str(uuid.uuid4())
        # Reading a body can outlast the run or its authorization, so admission is re-checked
        # under the engagement lock that cancellation also takes.
        try:
            self.ledger.reserve(
                request_id, call_id, maximum, time.time(),
                guard=lambda: self._authorized(request_id, authorization, client_ip),
                model=payload["model"],
            )
        except budget.NotAdmitted:
            self.reply(handler, 401, _public_error("Unauthorized", "authentication_error"))
            return
        except budget.BudgetExhausted:
            self._mark_stopped(request_id, "budget-exhausted")
            self.reply(handler, 402, _public_error("Engagement inference budget exhausted", "insufficient_quota"))
            return
        except budget.BudgetError as exc:
            handler.log_message('broker: ledger refused reservation, %s', type(exc).__name__)
            self.reply(handler, 503, _public_error("Inference budget unavailable", "budget_unavailable"))
            return
        handler.log_message('broker: reserved %s micro-USD for call %s', maximum, call_id)
        meter = UsageMeter(stream=payload.get("stream") is True)
        outcome = "maximum:unknown"
        try:
            outcome = self._forward(handler, request_id, authorization, client_ip, body, api_key, meter)
        finally:
            meter.finish()
            actual = settled_charge(meter.usages, input_upper, max_tokens, price)
            if actual is None and outcome.startswith(RELEASED) and not meter.usages:
                # Proven never sent upstream intact: nothing can have been billed.
                actual = 0
            elif actual is None:
                actual = maximum
                if not outcome.startswith("maximum:"):
                    outcome = "maximum:" + ("no-usage" if not meter.usages else "usage-out-of-bounds")
            try:
                self.ledger.settle(request_id, call_id, actual, time.time(), outcome)
                handler.log_message('broker: settled call %s at %s micro-USD (%s)', call_id, actual, outcome)
            except budget.BudgetError as exc:
                # The reservation stays committed at its maximum.
                handler.log_message('broker: settlement failed for call %s, %s', call_id, type(exc).__name__)

    def _forward(self, handler, request_id, authorization, client_ip, body, api_key, meter) -> str:
        handler.log_message('broker: starting upstream request')
        # Connections are registered per call: stopping one call must never reset the
        # upstream connections of the engagement's other in-flight calls.
        call_key = f"{request_id}/{uuid.uuid4()}"
        stopped = threading.Event()
        upstream_holder = []

        def watch_headers():
            while not stopped.wait(self.watch_interval):
                if not self._authorized(request_id, authorization, client_ip):
                    stopped.set()
                    self._close_connections(call_key)
                    for upstream in upstream_holder:
                        upstream.close()

        if not self._authorized(request_id, authorization, client_ip):
            self.reply(handler, 401, _public_error("Unauthorized", "authentication_error"))
            return RELEASED + "stopped-before-forward"
        watcher = threading.Thread(target=watch_headers, name=f"broker-headers-{request_id}", daemon=True)
        watcher.start()
        try:
            transport = self.transport or (lambda payload, key: self._live_transport(call_key, payload, key))
            attempt = 1
            while True:
                try:
                    status, headers, upstream = transport(body, api_key)
                    break
                except UpstreamFailure as exc:
                    handler.log_message('broker: upstream attempt %d failed, %s', attempt, exc.describe())
                    if exc.sent or stopped.is_set() or attempt >= self.upstream_attempts:
                        raise
                # Never sent intact, so nothing was billed: retry on a fresh connection.
                if stopped.wait(self.retry_backoff * attempt) or not self._authorized(
                        request_id, authorization, client_ip):
                    stopped.set()
                    raise UpstreamFailure("retry", RuntimeError("stopped"), 0.0, sent=False)
                attempt += 1
            upstream_holder.append(upstream)
        except UpstreamFailure as exc:
            self.reply(handler, 502, _public_error("Upstream model call failed", "upstream_error"))
            if not exc.sent:
                return RELEASED + ("stopped-before-send" if stopped.is_set() else f"upstream-not-reached:{exc.phase}")
            # The whole request reached the upstream, which may have run and billed it.
            return "maximum:stopped-before-headers" if stopped.is_set() else "maximum:transport-error"
        except Exception as exc:
            handler.log_message('broker: upstream error %s', type(exc).__name__)
            self.reply(handler, 502, _public_error("Upstream model call failed", "upstream_error"))
            # Unclassified: the request may have reached the model before the failure.
            return "maximum:stopped-before-headers" if stopped.is_set() else "maximum:transport-error"
        finally:
            cancelled_while_waiting = stopped.is_set()
            stopped.set()
            if cancelled_while_waiting:
                self._close_connections(call_key)
            watcher.join(timeout=5)
        if not self._authorized(request_id, authorization, client_ip):
            if hasattr(upstream, "close"):
                upstream.close()
            self.reply(handler, 401, _public_error("Unauthorized", "authentication_error"))
            return "maximum:stopped-before-body"
        handler.log_message('broker: upstream response received %s', status)
        if isinstance(upstream, (bytes, str)):
            data = upstream.encode() if isinstance(upstream, str) else upstream
            meter.feed(data)
            self.reply(handler, status, data, headers.get("Content-Type", "application/json"))
            return "usage"
        return self._relay(handler, request_id, authorization, client_ip, status, headers, upstream, meter)

    def _relay(self, handler, request_id, authorization, client_ip, status, headers, upstream, meter) -> str:
        stop = threading.Event()
        reasons = []

        def still_authorized() -> bool:
            return self._authorized(request_id, authorization, client_ip)

        def watch():
            while not stop.wait(self.watch_interval):
                if not still_authorized():
                    reasons.append("maximum:stopped-while-idle")
                    stop.set()
                    upstream.close()

        watcher = threading.Thread(target=watch, name=f"broker-watch-{request_id}", daemon=True)
        watcher.start()
        completed = False
        try:
            content_type = headers.get("Content-Type", headers.get("content-type", "application/json"))
            handler.send_response(status)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            while not stop.is_set():
                try:
                    chunk = upstream.read(64 * 1024)
                except (OSError, ValueError, http.client.HTTPException):
                    break
                if not chunk:
                    completed = not stop.is_set()
                    break
                meter.feed(chunk)
                if not still_authorized():
                    reasons.append("maximum:stopped-mid-stream")
                    break
                handler.wfile.write(f"{len(chunk):X}\r\n".encode())
                handler.wfile.write(chunk + b"\r\n")
            if completed:
                handler.wfile.write(b"0\r\n\r\n")
            else:
                # A truncated chunked body tells the worker the response did not finish.
                handler.close_connection = True
        except OSError:
            reasons.append("maximum:client-disconnected")
            handler.close_connection = True
        finally:
            stop.set()
            upstream.close()
            watcher.join(timeout=5)
        handler.log_message('broker: response relay finished')
        if completed:
            return "usage"
        # A usage chunk that already arrived still settles the call; otherwise the maximum stays.
        return "usage-after-stop" if meter.usages else (reasons[0] if reasons else "maximum:upstream-closed")

    @staticmethod
    def reply(handler, status: int, body, content_type: str = "application/json") -> None:
        encoded = body if isinstance(body, bytes) else _json_bytes(body)
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(encoded)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(encoded)
