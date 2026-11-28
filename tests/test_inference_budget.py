"""Durable per-engagement inference budget: ledger semantics and broker enforcement."""

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from engagement import budget
from engagement.api import make_broker_handler
from engagement.broker import MAX_BODY, MAX_OUTPUT_TOKENS, MODEL_ID, ModelBroker, _HttpUpstream
from engagement.credential import issue, revoke
from engagement.models import EngagementRequest
from engagement.store import EngagementStore
from tests.test_engagement_foundation import RUN_KEY, request_dict

TOKEN = "synthetic-engagement-token"
REPO = Path(__file__).resolve().parent.parent
PRICE = (budget.LIST_INPUT_MICROUSD_PER_MTOK, budget.LIST_OUTPUT_MICROUSD_PER_MTOK)


def make_engagement(store: EngagementStore) -> str:
    request = EngagementRequest.from_dict(request_dict(inference_key=RUN_KEY))
    store.create(request)
    store.update(request.id, status="running")
    issue(store._dir(request.id), TOKEN, 60, allowed_ips={"127.0.0.1"})
    return request.id


def sse(*events) -> list[bytes]:
    return [b"data: " + json.dumps(event).encode() + b"\n\n" for event in events] + [b"data: [DONE]\n\n"]


def delta(text):
    return {"choices": [{"index": 0, "delta": {"content": text}}], "usage": None}


def final_usage(prompt, completion):
    return {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                                     "total_tokens": prompt + completion}}


class FakeUpstream:
    """A streaming upstream. With `hold`, it blocks after its chunks until closed."""

    def __init__(self, chunks, hold=False):
        self.chunks = list(chunks)
        self.hold = hold
        self.closed = threading.Event()
        self.served = threading.Event()

    def read(self, _amount):
        if self.chunks:
            return self.chunks.pop(0)
        self.served.set()
        if self.hold:
            self.closed.wait(10)
        return b""

    def close(self):
        self.closed.set()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EngagementStore(self.temp.name)
        self.engagement = make_engagement(self.store)
        self.ledger = budget.InferenceLedger(self.store, cap_microusd=1_000)

    def tearDown(self):
        self.temp.cleanup()

    def committed(self, ledger=None, engagement=None):
        return (ledger or self.ledger).snapshot(engagement or self.engagement)["committedMicroUsd"]

    def test_concurrent_reservations_for_the_last_headroom_admit_exactly_one(self):
        self.ledger.reserve(self.engagement, "seed", 600, 0)
        for attempt in range(20):
            results = []
            barrier = threading.Barrier(2)
            ledger = budget.InferenceLedger(self.store, cap_microusd=1_000)

            def contend(name):
                barrier.wait()
                try:
                    ledger.reserve(self.engagement, name, 300, 0)
                    results.append("admitted")
                except budget.BudgetExhausted:
                    results.append("refused")

            threads = [threading.Thread(target=contend, args=(f"{attempt}-{n}",)) for n in "ab"]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(results), ["admitted", "refused"])
            snapshot = ledger.snapshot(self.engagement)
            self.assertEqual(snapshot["committedMicroUsd"], 900)
            winner = next(n for n in snapshot["reservations"] if n.startswith(f"{attempt}-"))
            ledger.settle(self.engagement, winner, 0, 1, "test")  # Reopen 300 for the next round.
            self.assertEqual(self.committed(ledger), 600)

    def test_concurrent_processes_for_the_last_headroom_admit_exactly_one(self):
        self.ledger.reserve(self.engagement, "seed", 700, 0)
        script = (
            "import sys, time\n"
            "from engagement import budget\n"
            "from engagement.store import EngagementStore\n"
            "ledger = budget.InferenceLedger(EngagementStore(sys.argv[1]), cap_microusd=1000)\n"
            "while time.time() < float(sys.argv[4]): pass\n"
            "try:\n"
            "    ledger.reserve(sys.argv[2], sys.argv[3], 300, 0); print('admitted')\n"
            "except budget.BudgetExhausted:\n"
            "    print('refused')\n"
        )
        start = str(time.time() + 1.0)
        processes = [
            subprocess.Popen([sys.executable, "-B", "-c", script, self.temp.name, self.engagement, name, start],
                             cwd=REPO, stdout=subprocess.PIPE, text=True)
            for name in ("p1", "p2", "p3")
        ]
        outputs = sorted(process.communicate(timeout=30)[0].strip() for process in processes)
        self.assertEqual(outputs, ["admitted", "refused", "refused"])
        self.assertEqual(self.committed(), 1_000)

    def test_reservation_replay_is_refused_and_changes_nothing(self):
        self.ledger.reserve(self.engagement, "call-1", 100, 0)
        before = self.ledger.snapshot(self.engagement)
        with self.assertRaises(budget.ReservationReplay):
            self.ledger.reserve(self.engagement, "call-1", 100, 0)
        self.ledger.settle(self.engagement, "call-1", 40, 1, "usage")
        with self.assertRaises(budget.ReservationReplay):
            self.ledger.reserve(self.engagement, "call-1", 100, 0)
        after = self.ledger.snapshot(self.engagement)
        self.assertEqual(after["requestCount"], before["requestCount"])
        self.assertEqual(after["committedMicroUsd"], 40)

    def test_settlement_replay_is_idempotent_and_a_conflict_is_an_error(self):
        self.ledger.reserve(self.engagement, "call-1", 100, 0)
        self.ledger.settle(self.engagement, "call-1", 40, 1, "usage")
        self.ledger.settle(self.engagement, "call-1", 40, 2, "usage")
        self.assertEqual(self.committed(), 40)
        with self.assertRaises(budget.SettlementConflict):
            self.ledger.settle(self.engagement, "call-1", 41, 3, "usage")
        with self.assertRaises(budget.SettlementConflict):
            self.ledger.settle(self.engagement, "call-1", 100, 3, "maximum")
        self.assertEqual(self.committed(), 40)

    def test_settlement_outside_the_reservation_or_unknown_id_is_an_error(self):
        self.ledger.reserve(self.engagement, "call-1", 100, 0)
        for actual in (101, -1, True, 1.5, None):
            with self.subTest(actual=actual), self.assertRaises(budget.SettlementConflict):
                self.ledger.settle(self.engagement, "call-1", actual, 1, "usage")
        with self.assertRaises(budget.SettlementConflict):
            self.ledger.settle(self.engagement, "never-reserved", 0, 1, "usage")
        self.assertEqual(self.committed(), 100)

    def test_a_crash_between_reserve_and_settle_keeps_the_maximum_committed(self):
        script = (
            "import os, sys\n"
            "from engagement import budget\n"
            "from engagement.store import EngagementStore\n"
            "budget.InferenceLedger(EngagementStore(sys.argv[1])).reserve(sys.argv[2], 'crashed', 777, 0)\n"
            "os._exit(9)\n"
        )
        result = subprocess.run([sys.executable, "-B", "-c", script, self.temp.name, self.engagement], cwd=REPO)
        self.assertEqual(result.returncode, 9)
        restarted = budget.InferenceLedger(self.store)
        snapshot = restarted.snapshot(self.engagement)
        self.assertEqual(snapshot["committedMicroUsd"], 777)
        self.assertEqual(snapshot["requestCount"], 1)
        self.assertIsNone(snapshot["reservations"]["crashed"]["actualMicroUsd"])
        self.assertFalse(budget.ledger_path(self.store, self.engagement).with_suffix(".tmp").exists())

    def test_request_count_is_recorded_but_never_limits(self):
        # Dollars are the only limit: 300 one-micro-USD calls fit, the dollar cap then refuses.
        ledger = budget.InferenceLedger(self.store, cap_microusd=300)
        for index in range(300):
            ledger.reserve(self.engagement, f"call-{index}", 1, 0)
        self.assertEqual(ledger.snapshot(self.engagement)["requestCount"], 300)
        with self.assertRaises(budget.BudgetExhausted):
            ledger.reserve(self.engagement, "over", 1, 0)
        ledger.settle(self.engagement, "call-0", 0, 1, "usage")
        ledger.reserve(self.engagement, "after-settle", 1, 0)
        self.assertEqual(ledger.snapshot(self.engagement)["requestCount"], 301)
        self.assertNotIn("requestCap", ledger.snapshot(self.engagement))
        with self.assertRaises(ValueError):
            budget.InferenceLedger(self.store, cap_microusd=budget.HARD_CAP_MICROUSD + 1)

    def test_ledger_from_before_the_request_cap_was_removed_keeps_working(self):
        path = budget.ledger_path(self.store, self.engagement)
        legacy = {**budget._empty(budget.HARD_CAP_MICROUSD), "requestCap": 2}
        legacy["reservations"] = {
            call: {"maximumMicroUsd": 5, "actualMicroUsd": 5, "reservedAtUnix": 0,
                   "settledAtUnix": 1, "outcome": "usage", "model": None}
            for call in ("a", "b")
        }
        legacy.update(committedMicroUsd=10, requestCount=2)
        path.write_text(json.dumps(legacy))
        self.ledger.reserve(self.engagement, "c", 1, 0)
        stored = json.loads(path.read_text())
        self.assertNotIn("requestCap", stored)
        self.assertEqual((stored["requestCount"], stored["committedMicroUsd"]), (3, 11))

    def test_two_engagements_never_share_or_consume_each_others_budget(self):
        other = make_engagement(self.store)
        self.ledger.reserve(self.engagement, "a-1", 1_000, 0)
        with self.assertRaises(budget.BudgetExhausted):
            self.ledger.reserve(self.engagement, "a-2", 1, 0)
        self.ledger.reserve(other, "b-1", 1_000, 0)
        with self.assertRaises(budget.SettlementConflict):
            self.ledger.settle(other, "a-1", 0, 1, "usage")  # A's call id means nothing in B.
        self.ledger.settle(self.engagement, "a-1", 10, 1, "usage")
        self.assertEqual(self.committed(), 10)
        self.assertEqual(self.committed(engagement=other), 1_000)
        self.assertNotEqual(budget.ledger_path(self.store, self.engagement), budget.ledger_path(self.store, other))

    def test_corrupt_or_tampered_ledger_fails_closed(self):
        self.ledger.reserve(self.engagement, "a", 100, 0)
        path = budget.ledger_path(self.store, self.engagement)
        ledger = json.loads(path.read_text())
        for tamper in (
            lambda d: d.update(committedMicroUsd=0),
            lambda d: d.update(requestCount=0),
            lambda d: d.update(capMicroUsd=budget.HARD_CAP_MICROUSD + 1),
            lambda d: d["reservations"]["a"].update(actualMicroUsd=101),
        ):
            copy = json.loads(json.dumps(ledger))
            tamper(copy)
            path.write_text(json.dumps(copy))
            with self.subTest(copy=copy), self.assertRaises(budget.LedgerCorrupt):
                self.ledger.reserve(self.engagement, "b", 1, 0)
        path.write_text("{not json")
        with self.assertRaises(budget.LedgerCorrupt):
            self.ledger.reserve(self.engagement, "b", 1, 0)


class PricingTests(unittest.TestCase):
    def test_list_price_and_raise_only_override(self):
        self.assertEqual(budget.prices({}), (300_000, 1_200_000))
        self.assertEqual(budget.prices({budget.INPUT_PRICE_ENV: "300001"}), (300_001, 1_200_000))
        for bad in ("299999", "0", "-1", "1e9", "abc", " 300000", "３００００００"):
            with self.subTest(bad=bad), self.assertRaises(budget.PricingError):
                budget.prices({budget.OUTPUT_PRICE_ENV: bad})

    def test_two_models_charge_landing_prices_to_one_ledger(self):
        self.assertEqual(budget.prices({}, budget.COORDINATOR_MODEL_ID), (2_500_000, 14_000_000))
        self.assertEqual(budget.prices({}, budget.WORKER_MODEL_ID), (300_000, 1_200_000))
        with tempfile.TemporaryDirectory() as root:
            store = EngagementStore(root)
            engagement = make_engagement(store)
            ledger = budget.InferenceLedger(store)
            for call, model in (("k3", budget.COORDINATOR_MODEL_ID), ("ds", budget.WORKER_MODEL_ID)):
                price = budget.prices({}, model)
                ledger.reserve(engagement, call, budget.charge_microusd(1_000_000, 1_000_000, price), 0, model=model)
                ledger.settle(engagement, call, budget.charge_microusd(1_000_000, 1_000_000, price), 1, "usage")
            snapshot = ledger.snapshot(engagement)
            self.assertEqual(snapshot["committedMicroUsd"], 16_500_000 + 1_500_000)
            self.assertEqual(snapshot["capMicroUsd"], budget.HARD_CAP_MICROUSD)

    def test_charge_rounds_up_to_whole_micro_usd(self):
        self.assertEqual(budget.charge_microusd(1, 0, PRICE), 1)  # 0.3 micro-USD rounds up.
        self.assertEqual(budget.charge_microusd(10, 0, PRICE), 3)
        self.assertEqual(budget.charge_microusd(1_000_000, 1_000_000, PRICE), 1_500_000)  # $0.30 + $1.20

    def test_the_largest_possible_request_reserves_about_a_dollar_thirty(self):
        upper = budget.input_token_upper_bound(b"x" * MAX_BODY)
        self.assertEqual(upper, MAX_BODY + budget.INPUT_TOKEN_OVERHEAD)
        self.assertEqual(budget.charge_microusd(upper, MAX_OUTPUT_TOKENS, PRICE), 1_302_528)
        # With no request cap, dollars alone bound a run: 767 maximum-size worker calls fit in $1,000.
        self.assertEqual(budget.HARD_CAP_MICROUSD // 1_302_528, 767)


class HttpUpstreamAbortTests(unittest.TestCase):
    def test_close_from_another_thread_unblocks_a_pending_read(self):
        listener = socket.create_server(("127.0.0.1", 0))
        release = threading.Event()

        def serve():
            peer, _ = listener.accept()
            with peer:
                peer.recv(65536)
                peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                             b"Transfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")
                release.wait(10)  # A silent upstream that is still generating.

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=10)
        connection.request("POST", "/v1/chat/completions", body=b"{}")
        upstream = _HttpUpstream(connection, connection.getresponse())
        self.assertEqual(upstream.read(65536), b"hello")
        outcome = {}

        def blocked_read():
            try:
                outcome["data"] = upstream.read(65536)
            except Exception as exc:  # Either an error or EOF ends the relay.
                outcome["error"] = type(exc).__name__

        reader = threading.Thread(target=blocked_read)
        reader.start()
        time.sleep(0.2)
        self.assertTrue(reader.is_alive())
        started = time.time()
        upstream.close()
        reader.join(5)
        self.assertFalse(reader.is_alive())
        self.assertLess(time.time() - started, 2)
        self.assertNotEqual(outcome.get("data"), b"hello")
        upstream.close()  # Idempotent.
        release.set()
        server.join(5)
        listener.close()


class BrokerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EngagementStore(self.temp.name)
        self.engagement = make_engagement(self.store)
        self.bodies = []
        self.responses = []
        self.environ = {}

        def transport(body, _api_key):
            self.bodies.append(json.loads(body))
            response = self.responses.pop(0) if self.responses else b'{"status":"ok"}'
            if callable(response):
                response = response()
            return 200, {"Content-Type": "text/event-stream"}, response

        self.broker = ModelBroker(self.store, transport=transport, environ=self.environ)
        self.broker.watch_interval = 0.05
        self.server = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
            ("127.0.0.1", 0), make_broker_handler(self.store, self.broker)
        )
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def call(self, body, engagement=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(
            "POST", f"/v1/engagements/{engagement or self.engagement}/chat/completions",
            body=json.dumps(body),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        )
        response = connection.getresponse()
        try:
            data = response.read()
        except http.client.IncompleteRead as exc:
            data = exc.partial + b"<truncated>"
        connection.close()
        return response.status, data

    def ledger(self, engagement=None):
        return self.broker.ledger.snapshot(engagement or self.engagement)

    def settled(self, engagement=None, count=1):
        deadline = time.time() + 5
        while time.time() < deadline:
            entries = list(self.ledger(engagement)["reservations"].values())
            if len(entries) >= count and all(e["actualMicroUsd"] is not None for e in entries):
                return entries
            time.sleep(0.01)
        self.fail("reservation was not settled")

    def body(self, **extra):
        return {"model": MODEL_ID, "messages": [{"role": "user", "content": "synthetic task"}], **extra}

    def test_stream_forces_usage_and_settles_from_the_final_usage_chunk(self):
        chunks = sse(delta("hel"), delta("lo"), final_usage(120, 7))
        self.responses.append(lambda: FakeUpstream(chunks))
        status, data = self.call(self.body(stream=True, stream_options={"include_usage": False}, max_tokens=100))
        self.assertEqual(status, 200)
        self.assertEqual(data, b"".join(sse(delta("hel"), delta("lo"), final_usage(120, 7))))
        self.assertEqual(self.bodies[0]["stream_options"], {"include_usage": True})
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(120, 7, PRICE))
        self.assertEqual(entry["outcome"], "usage")
        self.assertEqual(self.ledger()["committedMicroUsd"], entry["actualMicroUsd"])

    def test_usage_split_across_chunk_boundaries_still_parses(self):
        raw = b"".join(sse(delta("x"), final_usage(50, 3)))
        self.responses.append(lambda: FakeUpstream([raw[i:i + 7] for i in range(0, len(raw), 7)]))
        self.call(self.body(stream=True, max_tokens=10))
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(50, 3, PRICE))

    def test_non_stream_settles_from_body_usage(self):
        self.responses.append(json.dumps({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}).encode())
        self.assertEqual(self.call(self.body(max_tokens=10))[0], 200)
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(9, 4, PRICE))
        self.assertNotIn("stream_options", self.bodies[0])

    def test_missing_malformed_or_out_of_bound_usage_keeps_the_maximum(self):
        cases = {
            "no usage": lambda: FakeUpstream(sse(delta("x"))),
            "usage on a content chunk": lambda: FakeUpstream(sse({**delta("x"), "usage": {"prompt_tokens": 1, "completion_tokens": 1}})),
            "completion over max_tokens": lambda: FakeUpstream(sse(final_usage(10, 11))),
            "negative": lambda: FakeUpstream(sse(final_usage(-5, 1))),
            "string tokens": lambda: FakeUpstream(sse({"choices": [], "usage": {"prompt_tokens": "1", "completion_tokens": 1}})),
            "bad total": lambda: FakeUpstream(sse({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 1}})),
            "two usage chunks": lambda: FakeUpstream(sse(final_usage(1, 1), final_usage(2, 2))),
            "garbage": lambda: FakeUpstream([b"data: {not json\n\n"]),
        }
        for index, (name, response) in enumerate(cases.items(), start=1):
            with self.subTest(name):
                self.responses.append(response)
                self.call(self.body(stream=True, max_tokens=10))
                entries = self.settled(count=index)
                entry = next(e for e in entries if e["reservedAtUnix"] == max(x["reservedAtUnix"] for x in entries))
                self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])
                self.assertTrue(entry["outcome"].startswith("maximum:"))
        self.responses.append(b'{"error":"upstream said no"}')
        self.call(self.body(max_tokens=10))
        entries = self.settled(count=len(cases) + 1)
        self.assertEqual(self.ledger()["committedMicroUsd"], sum(e["maximumMicroUsd"] for e in entries))

    def test_transport_failure_keeps_the_maximum(self):
        def explode():
            raise ConnectionResetError("synthetic")
        self.responses.append(explode)
        self.assertEqual(self.call(self.body(max_tokens=10))[0], 502)
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])
        self.assertEqual(entry["outcome"], "maximum:transport-error")

    def test_reservation_covers_body_bytes_and_max_tokens(self):
        self.call(self.body(max_tokens=10))
        [entry] = self.settled()
        sent = json.dumps(self.bodies[0], separators=(",", ":")).encode()
        self.assertEqual(entry["maximumMicroUsd"],
                         budget.charge_microusd(len(sent) + budget.INPUT_TOKEN_OVERHEAD, 10, PRICE))

    def test_cancel_while_upstream_is_silent_closes_the_upstream(self):
        upstream = FakeUpstream(sse(delta("first")), hold=True)
        upstream.chunks.pop()  # Leave the stream open: no [DONE].
        self.responses.append(lambda: upstream)
        result = {}
        caller = threading.Thread(target=lambda: result.update(r=self.call(self.body(stream=True, max_tokens=10))))
        caller.start()
        self.assertTrue(upstream.served.wait(5))
        self.store.request_cancel(self.engagement)
        self.assertTrue(upstream.closed.wait(5), "watchdog did not close the upstream")
        caller.join(5)
        status, data = result["r"]
        self.assertEqual(status, 200)
        self.assertTrue(data.endswith(b"<truncated>"))
        [entry] = self.settled()
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])
        self.assertEqual(entry["outcome"], "maximum:stopped-while-idle")

    def test_revocation_between_chunks_stops_the_relay(self):
        state = {"n": 0}

        class Endless(FakeUpstream):
            def read(inner, _amount):
                state["n"] += 1
                if state["n"] == 3:
                    revoke(self.store._dir(self.engagement))
                return b"data: " + json.dumps(delta("x")).encode() + b"\n\n"

        upstream = Endless([])
        self.responses.append(lambda: upstream)
        _status, data = self.call(self.body(stream=True, max_tokens=10))
        self.assertTrue(upstream.closed.is_set())
        self.assertTrue(data.endswith(b"<truncated>"))
        self.assertEqual(data.count(b"data: "), 2)  # The chunk read after revocation is not relayed.
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "maximum:stopped-mid-stream")
        self.assertEqual(entry["actualMicroUsd"], entry["maximumMicroUsd"])

    def test_deadline_mid_stream_settles_from_usage_already_received(self):
        state = {"n": 0}
        chunks = sse(final_usage(10, 2))[:1]

        class AfterUsage(FakeUpstream):
            def read(inner, amount):
                state["n"] += 1
                if state["n"] == 2:
                    self.store.update(self.engagement, deadlineAtUnix=time.time() - 1)
                    return b"data: " + json.dumps(delta("late")).encode() + b"\n\n"
                return FakeUpstream.read(inner, amount)

        self.responses.append(lambda: AfterUsage(chunks, hold=True))
        self.call(self.body(stream=True, max_tokens=10))
        [entry] = self.settled()
        self.assertEqual(entry["outcome"], "usage-after-stop")
        self.assertEqual(entry["actualMicroUsd"], budget.charge_microusd(10, 2, PRICE))

    def test_two_concurrent_calls_for_the_last_headroom_admit_exactly_one(self):
        maximum = budget.charge_microusd(
            len(json.dumps(self.body(max_tokens=10, stream=False), separators=(",", ":"))) + budget.INPUT_TOKEN_OVERHEAD,
            10, PRICE)
        self.broker.ledger.cap = maximum + maximum // 2
        gate = threading.Event()

        def slow():
            gate.wait(5)
            return b'{"status":"ok"}'

        self.responses.extend([slow, slow])
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.call(self.body(max_tokens=10, stream=False))[0]))
                   for _ in range(2)]
        for thread in threads:
            thread.start()
        deadline = time.time() + 5
        while len(results) < 1 and time.time() < deadline:
            time.sleep(0.01)
        gate.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(results), [200, 402])
        self.assertEqual(len(self.bodies), 1)

    def test_budget_and_request_count_survive_a_controller_restart(self):
        self.call(self.body(max_tokens=10))
        self.call(self.body(max_tokens=10))
        self.settled(count=2)
        committed = self.ledger()["committedMicroUsd"]
        restarted = ModelBroker(self.store, transport=lambda body, _api_key: (200, {}, b"{}"), environ={})
        snapshot = restarted.ledger.snapshot(self.engagement)
        self.assertEqual((snapshot["committedMicroUsd"], snapshot["requestCount"]), (committed, 2))
        self.broker.ledger = restarted.ledger  # Serve the next call from the new process's ledger.
        status, _ = self.call(self.body(max_tokens=10))
        self.assertEqual(status, 200)  # No request-count cap; the dollar cap has headroom.
        self.assertEqual(len(self.bodies), 3)
        self.settled(count=3)

    def test_exhausted_engagement_does_not_block_another(self):
        other = make_engagement(self.store)
        self.broker.ledger.cap = 1
        self.assertEqual(self.call(self.body(max_tokens=10))[0], 402)
        self.broker.ledger.cap = budget.HARD_CAP_MICROUSD
        self.assertEqual(self.call(self.body(max_tokens=10), engagement=other)[0], 200)
        self.assertEqual(self.ledger()["requestCount"], 0)
        self.assertEqual(self.ledger(other)["requestCount"], 1)

    def test_lowered_price_override_refuses_every_paid_call(self):
        self.environ[budget.INPUT_PRICE_ENV] = "1"
        status, data = self.call(self.body())
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(data)["error"]["type"], "budget_unavailable")
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.ledger()["requestCount"], 0)

    def test_inputs_that_break_the_token_bound_are_rejected_before_reserving(self):
        image = [{"type": "image_url", "image_url": {"url": "https://example.invalid/a.png"}}]
        for name, body in {
            "image part": {"model": MODEL_ID, "messages": [{"role": "user", "content": image}]},
            "image in tool result": {"model": MODEL_ID, "messages": [{"role": "tool", "tool_call_id": "1", "content": image}]},
            "n": self.body(n=2),
            "best_of": self.body(best_of=4),
            "chat_template": self.body(chat_template="{% for i in range(9999999) %}x{% endfor %}"),
            "max_completion_tokens": self.body(max_completion_tokens=MAX_OUTPUT_TOKENS + 1),
            "stream non-bool": self.body(stream="yes"),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.call(body)[0], 400)
        self.assertEqual(self.bodies, [])
        self.assertEqual(self.ledger()["requestCount"], 0)

    def test_max_completion_tokens_is_normalized_to_the_smaller_limit(self):
        self.call(self.body(max_tokens=500, max_completion_tokens=20))
        self.assertEqual(self.bodies[0]["max_tokens"], 20)
        self.assertEqual(self.bodies[0]["max_completion_tokens"], 20)

    def test_successful_headers_do_not_close_other_concurrent_calls(self):
        from engagement.broker import UsageMeter
        self.broker.transport = None
        arrivals = threading.Barrier(2)
        payload = json.dumps({"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2}}).encode()

        def transport(_request_id, _body, _key):
            arrivals.wait(3)
            return 200, {"Content-Type": "application/json"}, payload

        def invoke():
            handler = Mock()
            handler.wfile = __import__("io").BytesIO()
            with patch.object(self.broker, "_live_transport", side_effect=transport), \
                 patch.object(self.broker, "_close_connections", wraps=self.broker._close_connections) as close:
                result = self.broker._forward(
                    handler, self.engagement, f"Bearer {TOKEN}", "127.0.0.1", b"{}", RUN_KEY,
                    UsageMeter(False),
                )
                return result, close

        first, second = {}, {}
        thread = threading.Thread(target=lambda: second.update(result=invoke()))
        thread.start()
        first.update(result=invoke())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        for outcome in (first["result"], second["result"]):
            result, close = outcome
            self.assertEqual(result, "usage")
            close.assert_not_called()

    def test_cancel_after_reservation_prevents_upstream_forwarding(self):
        original = self.broker.ledger.reserve
        def reserve_then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            self.store.request_cancel(self.engagement)
            return result
        with patch.object(self.broker.ledger, "reserve", side_effect=reserve_then_cancel):
            status, _ = self.call(self.body())
        self.assertEqual(status, 401)
        self.assertEqual(self.bodies, [])
        [entry] = self.settled()
        # Nothing was forwarded, so nothing can have been billed: the reservation is released.
        self.assertEqual(entry["actualMicroUsd"], 0)
        self.assertEqual(entry["outcome"], "released:stopped-before-forward")
        self.assertEqual(self.ledger()["committedMicroUsd"], 0)

    def test_cancel_lands_before_or_after_reservation_never_between(self):
        self.store.request_cancel(self.engagement)
        self.assertEqual(self.call(self.body())[0], 401)
        self.assertEqual(self.ledger()["requestCount"], 0)


if __name__ == "__main__":
    unittest.main()
