"""Explicit-state check of the real InferenceLedger, plus a z3 proof of its arithmetic.

Adapted from the review models (agenthub scratch review/models.py). The BFS drives the actual
engagement/budget.py code from every reachable ledger file through every operation, and checks
each transition against an independent oracle. Run standalone for the printed summary:

    python3 -I tests/test_budget_model.py   (z3-solver installed => the proof runs too)
"""

from collections import deque
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engagement import budget  # noqa: E402
from engagement.models import AGREEMENT_VERSION, EngagementRequest  # noqa: E402
from engagement.store import EngagementStore  # noqa: E402

# The standalone run explores FULL; the unittest run explores SMALL to stay fast.
FULL = dict(cap=4, calls=("c0", "c1", "c2", "c3"), maxima=(1, 2, 3))
SMALL = dict(cap=3, calls=("c0", "c1", "c2"), maxima=(1, 2))
CAP = 4  # For the negative control.


def _ops(calls, maxima):
    for call in calls:
        for maximum in maxima:
            yield ("reserve", call, maximum)
        for actual in range(0, max(maxima) + 2):
            yield ("settle", call, actual)


def _oracle(before, op, cap):
    """What the ledger must do: the new state, or None when the op must be refused unchanged."""
    summary = _summary(before)
    entries = dict(summary["reservations"]) if summary else {}
    committed = summary["committedMicroUsd"] if summary else 0
    kind, call, value = op
    if kind == "reserve":
        if call in entries or value > cap - committed:
            return None
        entries[call] = (value, None)
        return committed + value, entries
    if call not in entries:
        return None
    maximum, actual = entries[call]
    if actual is not None or not 0 <= value <= maximum:
        return None  # Identical replays are no-ops, which the caller also checks as "unchanged".
    entries[call] = (maximum, value)
    return committed - maximum + value, entries


def _summary(ledger):
    if ledger is None:
        return None
    return {
        "committedMicroUsd": ledger["committedMicroUsd"],
        "reservations": {k: (v["maximumMicroUsd"], v["actualMicroUsd"]) for k, v in ledger["reservations"].items()},
    }


def explore(cap, calls, maxima):
    """BFS over real ledger files. Returns (states, transitions, violations)."""
    with tempfile.TemporaryDirectory() as root, mock.patch("os.fsync"):
        store = EngagementStore(root)
        request = EngagementRequest.from_dict({
            "id": "00000000-0000-4000-8000-000000000001",
            "target": {"hostname": "app.example.com"},
            "authorization": {"acceptedAt": "2026-11-25T00:00:00+00:00",
                              "contactEmail": "security@example.com", "agreementVersion": AGREEMENT_VERSION},
        })
        store.create(request)
        engagement = request.id
        path = budget.ledger_path(store, engagement)
        ledger = budget.InferenceLedger(store, cap_microusd=cap)

        def load(raw):
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(raw)

        def read():
            return path.read_text() if path.exists() else None

        seen = {None: None}
        queue = deque([None])
        violations, transitions = [], 0
        while queue:
            raw = queue.popleft()
            before = json.loads(raw) if raw else None
            if before is not None:
                try:
                    budget.check_invariant(before)
                except budget.LedgerCorrupt as exc:
                    violations.append(("invariant", raw, str(exc)))
                    continue
            for op in _ops(calls, maxima):
                load(raw)
                transitions += 1
                try:
                    if op[0] == "reserve":
                        ledger.reserve(engagement, op[1], op[2], 0)
                    else:
                        ledger.settle(engagement, op[1], op[2], 0, "model")
                except budget.BudgetError:
                    pass
                after_raw = read()
                after = json.loads(after_raw) if after_raw else None
                expected = _oracle(before, op, cap)
                summary = _summary(after)
                if expected is None:
                    if after_raw != raw:
                        violations.append(("refused op changed state", raw, op))
                else:
                    committed, entries = expected
                    if summary != {"committedMicroUsd": committed, "reservations": entries}:
                        violations.append(("op disagrees with oracle", raw, op, summary))
                # Restart: a fresh ledger object reads exactly what was written.
                if after is not None and _summary(budget.InferenceLedger(store).snapshot(engagement)) != summary:
                    violations.append(("restart changed state", raw, op))
                # Settled actuals never change and spend is never released below settled actuals.
                for call, (maximum, actual) in (_summary(before) or {"reservations": {}})["reservations"].items():
                    now = summary["reservations"].get(call) if summary else None
                    if now is None or now[0] != maximum or (actual is not None and now[1] != actual):
                        violations.append(("reservation lost or rewritten", raw, op))
                if after_raw not in seen:
                    seen[after_raw] = (raw, op)
                    queue.append(after_raw)
        # A crash inside _write leaves only a stray .tmp file; the ledger is unaffected.
        load(json.dumps(budget._empty(cap)))
        path.with_suffix(".tmp").write_text("{partial")
        ledger.reserve(engagement, "after-crash", 1, 0)
        if ledger.snapshot(engagement)["committedMicroUsd"] != 1:
            violations.append(("stray temp file affected the ledger",))
        return len(seen), transitions, violations


def unlocked_negative_control():
    """Two controllers each run load -> check -> save as separate steps, reserving 3 of a cap of
    4. Without the flock the shortest interleaving admits both (6 micro-USD of real spend, and a
    lost update hides it in the file); with it, no interleaving admits more than the cap."""
    def run(locked):
        # (committed in file, admitted spend, pc0, pc1, snapshot0, snapshot1, lock holder)
        init = (0, 0, 0, 0, None, None, None)
        seen, queue = {init}, deque([init])
        while queue:
            state = queue.popleft()
            committed, spent, pcs, snaps, holder = state[0], state[1], list(state[2:4]), list(state[4:6]), state[6]
            if spent > CAP:
                return False
            for p in (0, 1):
                new_pcs, new_snaps = pcs.copy(), snaps.copy()
                if pcs[p] == 0 and (not locked or holder is None):
                    new_pcs[p], new_snaps[p] = 1, committed
                    nxt = (committed, spent, *new_pcs, *new_snaps, p if locked else None)
                elif pcs[p] == 1:
                    new_pcs[p] = 2
                    if 3 <= CAP - snaps[p]:
                        nxt = (snaps[p] + 3, spent + 3, *new_pcs, *new_snaps, None)
                    else:
                        nxt = (committed, spent, *new_pcs, *new_snaps, None)
                else:
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return True
    return run(locked=True), run(locked=False)


def z3_proof():
    try:
        import z3
    except ImportError:
        return None
    C, U, S, N, m, a, M, cap = z3.Ints("C U S N m a M cap")
    inv = lambda c, u, s, n: z3.And(c == u + s, 0 <= c, c <= cap, u >= 0, s >= 0, 0 <= n)
    base = z3.And(cap >= 1, cap <= budget.HARD_CAP_MICROUSD, inv(C, U, S, N))
    claims = {
        "reserve": z3.Implies(z3.And(base, m >= 1, m <= cap - C), inv(C + m, U + m, S, N + 1)),
        "settle": z3.Implies(z3.And(base, M >= 1, M <= U, a >= 0, a <= M), inv(C - M + a, U - M, S + a, N)),
        "settle never raises spend": z3.Implies(z3.And(base, M >= 1, M <= U, a >= 0, a <= M), C - M + a <= C),
    }
    results = {}
    for name, claim in claims.items():
        solver = z3.Solver()
        solver.add(z3.Not(claim))
        results[name] = solver.check() == z3.unsat
    return results


class LedgerModelCheck(unittest.TestCase):
    def test_every_reachable_ledger_state_satisfies_the_invariant(self):
        states, _transitions, violations = explore(**SMALL)
        self.assertEqual(violations, [])
        self.assertGreater(states, 50)

    def test_flock_is_what_prevents_overspend(self):
        self.assertEqual(unlocked_negative_control(), (True, False))

    def test_z3_inductive_proof(self):
        results = z3_proof()
        if results is None:
            self.skipTest("z3-solver is not installed for this interpreter")
        self.assertTrue(all(results.values()), results)


if __name__ == "__main__":
    states, transitions, violations = explore(**FULL)
    print(f"real ledger BFS (cap {FULL['cap']}, {len(FULL['calls'])} call ids, "
          f"maxima {FULL['maxima']}): "
          f"{states} distinct ledger states, {transitions} transitions, "
          f"{'OK' if not violations else 'VIOLATIONS ' + repr(violations[:3])}")
    locked, unlocked = unlocked_negative_control()
    print(f"interleaved load/check/save with flock: {'OK' if locked else 'OVERSPEND'}; "
          f"NEGATIVE CONTROL without flock: {'OK (unexpected)' if unlocked else 'OVERSPEND found (expected)'}")
    proof = z3_proof()
    if proof is None:
        print("z3 proof: skipped (z3 not installed)")
    else:
        for name, ok in proof.items():
            print(f"z3 proof ({name}, all integers): {'PROVED' if ok else 'FAILS'}")
    sys.exit(0 if not violations and locked and not unlocked and (proof is None or all(proof.values())) else 1)
