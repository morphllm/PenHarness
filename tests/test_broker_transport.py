"""Upstream transport failure modes, over real sockets, and how each one settles the ledger.

Settlement rules under test:
- the upstream provably never received the complete request (DNS/TCP/TLS failure, or a partial
  write of the single headers+body send): the reservation is released, and the call is retried
  on a fresh connection, because nothing can have been billed;
- the whole request was sent and the connection then failed before headers (reset, close): the
  upstream may have run and billed it, so the maximum is kept and nothing is retried;
- a stream cut after its usage chunk settles from that usage; one cut before it keeps the maximum;
- stopping one call never resets another call's upstream connection.
"""

import http.client
import http.server
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from engagement import broker as broker_module
from engagement import budget
from engagement.api import IPv6ThreadingHTTPServer, make_broker_handler
from engagement.broker import MODEL_ID, ModelBroker, UpstreamFailure, UsageMeter
from engagement.credential import allow_workers
from engagement.store import EngagementStore
from tests.test_engagement_foundation import RUN_KEY
from tests.test_inference_budget import TOKEN, final_usage, delta, make_engagement, sse

PRICE = budget.MODEL_PRICES[MODEL_ID]


def _read_request(conn: socket.socket) -> tuple[bytes, bytes]:
    """Read one HTTP request; returns (head, body) as far as the peer sent it."""
    data = b""
    conn.settimeout(5)
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return data, b""
        data += chunk
    head, body = data.split(b"\r\n\r\n", 1)
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    while len(body) < length:
        try:
            chunk = conn.recv(65536)
        except OSError:
            break
        if not chunk:
            break
        body += chunk
    return head, body


def _reset(conn: socket.socket) -> None:
    # SO_LINGER 0 makes close() send an RST, as a middlebox or edge reset does.
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    conn.close()


class FakeUpstreamServer:
    """A local TCP upstream. `behavior(conn, body)` answers each request; bodies are recorded."""

    def __init__(self, behavior):
        self.behavior = behavior
        self.bodies = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        try:
            _head, body = _read_request(conn)
            self.bodies.append(body)
            self.behavior(conn, body)
        except OSError:
            pass

    def close(self):
        self.listener.close()


def respond_sse(conn, events, cut=False):
    payload = b"".join(sse(*events))
    head = (b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
    conn.sendall(head + f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
    if cut:
        time.sleep(0.05)
        _reset(conn)
        return
    conn.sendall(b"0\r\n\r\n")
    time.sleep(0.05)
    conn.close()


def closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class PlainConnections:
    """Stands in for http.client.HTTPSConnection so the real http.client request path runs
    over plain local sockets. Each connection goes to the next port in `ports` (the last one
    repeats); `wrap` may subclass the connection to inject a failure."""

    def __init__(self, ports, wrap=None):
        self.ports = list(ports)
        self.wrap = wrap
        self.made = 0

    def __call__(self, _host, _port, timeout=None):
        port = self.ports[min(self.made, len(self.ports) - 1)]
        self.made += 1
        cls = self.wrap(http.client.HTTPConnection) if self.wrap else http.client.HTTPConnection
        return cls("127.0.0.1", port, timeout=5)


class TransportFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EngagementStore(self.temp.name)
        self.engagement = make_engagement(self.store)
        self.broker = ModelBroker(self.store, environ={})
        self.broker.watch_interval = 0.05
        self.broker.retry_backoff = 0.01
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_broker_handler(self.store, self.broker))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.upstreams = []
        env = patch.dict(os.environ, {"HACK_ME_MORPH_BASE_URL": "https://api.example.test"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        for upstream in self.upstreams:
            upstream.close()
        self.temp.cleanup()

    def upstream(self, behavior):
        server = FakeUpstreamServer(behavior)
        self.upstreams.append(server)
        return server

    def call(self, stream=True, max_tokens=50):
        body = {"model": MODEL_ID, "messages": [{"role": "user", "content": "synthetic"}],
                "max_tokens": max_tokens, "stream": stream}
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("POST", f"/v1/engagements/{self.engagement}/chat/completions", body=json.dumps(body),
                           headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
        response = connection.getresponse()
        try:
            data = response.read()
        except http.client.IncompleteRead as exc:
            data = exc.partial
        connection.close()
        return response.status, data

    def settled(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            entries = list(self.broker.ledger.snapshot(self.engagement)["reservations"].values())
            if entries and all(e["actualMicroUsd"] is not None for e in entries):
                return entries
            time.sleep(0.01)
        self.fail("reservation was not settled")

    def committed(self):
        return self.broker.ledger.snapshot(self.engagement)["committedMicroUsd"]

    # -- never sent: released and retried ------------------------------------------------------

    def test_connection_refused_on_every_attempt_releases_the_reservation(self):
        connections = PlainConnections([closed_port()])
        with patch.object(broker_module.http.client, "HTTPSConnection", connections):
            status, _ = self.call()
        self.assertEqual(status, 502)
        self.assertEqual(connections.made, broker_module.UPSTREAM_ATTEMPTS)
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], 0)
        self.assertEqual(entry["outcome"], "released:upstream-not-reached:connect")
        self.assertEqual(self.committed(), 0)

    def test_tls_handshake_reset_releases_the_reservation(self):
        # The real HTTPSConnection: the peer resets during the handshake, before any request byte.
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        seen = []

        def reset_all():
            while True:
                try:
                    conn, _ = listener.accept()
                except OSError:
                    return
                conn.settimeout(2)
                try:
                    seen.append(conn.recv(65536))
                except OSError:
                    pass
                _reset(conn)

        threading.Thread(target=reset_all, daemon=True).start()
        self.addCleanup(listener.close)
        with patch.dict(os.environ, {"HACK_ME_MORPH_BASE_URL": f"https://127.0.0.1:{port}"}):
            status, _ = self.call()
        self.assertEqual(status, 502)
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "released:upstream-not-reached:connect")
        self.assertEqual(entry["actualMicroUsd"], 0)
        # Only TLS ClientHellos reached the peer, never an HTTP request.
        self.assertTrue(seen)
        self.assertTrue(all(not data.startswith(b"POST") for data in seen))

    def test_partial_send_releases_and_the_upstream_only_holds_a_prefix(self):
        server = self.upstream(lambda conn, _body: _reset(conn))

        def half_send(base):
            class HalfSend(base):
                def send(self, data):
                    self.sock.sendall(data[: len(data) // 2])
                    raise BrokenPipeError(32, "synthetic broken pipe mid-body")
            return HalfSend

        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port], half_send)):
            status, _ = self.call()
        self.assertEqual(status, 502)
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "released:upstream-not-reached:send")
        self.assertEqual(entry["actualMicroUsd"], 0)
        deadline = time.time() + 2
        while len(server.bodies) < broker_module.UPSTREAM_ATTEMPTS and time.time() < deadline:
            time.sleep(0.01)
        for body in server.bodies:
            with self.assertRaises(ValueError):
                json.loads(body)  # A strict prefix of the JSON body is never a runnable request.

    def test_connect_failure_then_success_settles_once_from_usage(self):
        server = self.upstream(lambda conn, _body: respond_sse(conn, [delta("hi"), final_usage(40, 2)]))
        connections = PlainConnections([closed_port(), server.port])
        with patch.object(broker_module.http.client, "HTTPSConnection", connections):
            status, data = self.call()
        self.assertEqual(status, 200)
        self.assertIn(b'"prompt_tokens": 40', data)
        self.assertEqual(connections.made, 2)
        self.assertEqual(len(server.bodies), 1)  # The model saw the request exactly once.
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "usage")
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(40, 2, PRICE))

    # -- sent in full: maximum kept, never retried ---------------------------------------------

    def test_reset_after_the_full_request_keeps_the_maximum_and_does_not_retry(self):
        server = self.upstream(lambda conn, _body: _reset(conn))
        connections = PlainConnections([server.port])
        with patch.object(broker_module.http.client, "HTTPSConnection", connections):
            status, _ = self.call()
        self.assertEqual(status, 502)
        self.assertEqual(connections.made, 1)
        self.assertEqual(len(server.bodies), 1)
        json.loads(server.bodies[0])  # The upstream had the complete request and could bill it.
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "maximum:transport-error")
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])

    def test_clean_close_before_headers_keeps_the_maximum(self):
        server = self.upstream(lambda conn, _body: conn.close())  # http.client: RemoteDisconnected
        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port])):
            self.assertEqual(self.call()[0], 502)
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "maximum:transport-error")
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])

    # -- mid-stream ---------------------------------------------------------------------------

    def test_stream_reset_after_the_usage_chunk_settles_from_usage(self):
        server = self.upstream(lambda conn, _b: respond_sse(conn, [delta("x"), final_usage(30, 3)], cut=True))
        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port])):
            self.call()
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "usage-after-stop")
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(30, 3, PRICE))

    def test_stream_reset_before_the_usage_chunk_keeps_the_maximum(self):
        def no_usage(conn, _body):
            payload = b"data: " + json.dumps(delta("x")).encode() + b"\n\n"
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n"
                         + f"{len(payload):X}\r\n".encode() + payload + b"\r\n")
            time.sleep(0.05)
            _reset(conn)
        server = self.upstream(no_usage)
        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port])):
            self.call()
        [entry] = self.settled()
        self.assertTrue(entry["outcome"].startswith("maximum:"))
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])

    # -- cancellation and isolation ---------------------------------------------------------

    def test_cancel_while_connecting_releases_and_sends_nothing(self):
        server = self.upstream(lambda conn, _body: respond_sse(conn, [final_usage(1, 1)]))
        entered = threading.Event()

        def stuck_connect(base):
            class Stuck(base):
                def connect(self):
                    entered.set()
                    self._closed_event = getattr(self, "_closed_event", threading.Event())
                    self._closed_event.wait(5)
                    raise ConnectionAbortedError(103, "closed while connecting")

                def close(self):
                    getattr(self, "_closed_event", threading.Event()).set()
                    super().close()
            return Stuck

        def cancel_soon():
            entered.wait(5)
            self.store.request_cancel(self.engagement)

        threading.Thread(target=cancel_soon, daemon=True).start()
        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port], stuck_connect)):
            self.assertEqual(self.call()[0], 502)
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "released:stopped-before-send")
        self.assertEqual(entry["actualMicroUsd"], 0)
        self.assertEqual(server.bodies, [])

    def test_stopping_one_call_never_resets_another_calls_connection(self):
        # Two calls of one engagement from two worker IPs wait on headers. Revoking one IP stops
        # its call; the other's upstream connection must survive and settle from usage.
        allow_workers(self.store._dir(self.engagement), ["127.0.0.1", "127.0.0.2"])
        release = threading.Event()

        def slow_answer(conn, _body):
            release.wait(5)
            respond_sse(conn, [delta("ok"), final_usage(11, 1)])

        server = self.upstream(slow_answer)
        results = {}

        def run(ip):
            handler = Mock()
            handler.wfile = __import__("io").BytesIO()
            meter = UsageMeter(True)
            results[ip] = (self.broker._forward(handler, self.engagement, f"Bearer {TOKEN}", ip,
                                                b'{"model":"x"}', RUN_KEY, meter), meter)

        with patch.object(broker_module.http.client, "HTTPSConnection", PlainConnections([server.port])):
            threads = [threading.Thread(target=run, args=(ip,)) for ip in ("127.0.0.1", "127.0.0.2")]
            for thread in threads:
                thread.start()
            deadline = time.time() + 3
            while len(server.bodies) < 2 and time.time() < deadline:
                time.sleep(0.01)
            allow_workers(self.store._dir(self.engagement), ["127.0.0.1"])
            deadline = time.time() + 3
            while "127.0.0.2" not in results and time.time() < deadline:
                time.sleep(0.01)
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(results["127.0.0.2"][0], "maximum:stopped-before-headers")
        self.assertEqual(results["127.0.0.1"][0], "usage")
        self.assertEqual(len(results["127.0.0.1"][1].usages), 1)

    def test_failure_log_names_the_phase(self):
        failure = UpstreamFailure("headers", ConnectionResetError(104, "reset"), 0.4321, sent=True)
        self.assertEqual(failure.describe(),
                         "phase=headers sent=yes error=ConnectionResetError errno=104 after=432ms")


class BrokerListenBacklogTests(unittest.TestCase):
    def test_a_burst_of_worker_connects_is_not_reset_by_the_listen_backlog(self):
        # socketserver's default backlog of 5 resets (macOS) or drops (Linux) a burst of
        # simultaneous connects from one task worker before the broker can accept them.
        self.assertGreaterEqual(IPv6ThreadingHTTPServer.request_queue_size, 64)

        class Server(http.server.ThreadingHTTPServer):
            request_queue_size = IPv6ThreadingHTTPServer.request_queue_size

        server = Server(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)  # never accepts
        try:
            ok = 0
            sockets = []
            for _ in range(40):
                sock = socket.socket()
                sock.settimeout(1)
                try:
                    sock.connect(("127.0.0.1", server.server_port))
                    ok += 1
                except OSError:
                    pass
                sockets.append(sock)
            self.assertEqual(ok, 40)
        finally:
            for sock in sockets:
                sock.close()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
