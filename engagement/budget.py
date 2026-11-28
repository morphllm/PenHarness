"""Durable per-engagement inference budget: reserve a worst-case charge, settle actual usage.

Every paid model call reserves its maximum possible charge under the engagement's flock before
the broker forwards it, and settles exactly once from the upstream's reported usage. After every
write:

    committedMicroUsd == sum(maximum of unsettled) + sum(actual of settled)
    0 <= committedMicroUsd <= capMicroUsd
    requestCount == len(reservations)

Dollars are the only limit. requestCount is a record of how many calls were reserved, not a cap.

A crash between reserve and settle leaves the maximum committed, so a restart can only make the
ledger more conservative. Nothing here ever releases spend on uncertainty.

Semantics follow Landing's unwired drizzle/0141_hack_me_inference_grants.sql
(hack_me_reserve_inference / hack_me_settle_inference), held on the controller's state volume
because the broker has no route to Landing's Postgres.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import EngagementStore

# One paid engagement may never commit more than $1,000 of inference, across both models.
HARD_CAP_MICROUSD = 1_000_000_000

COORDINATOR_MODEL_ID = "morph-kimik3"
WORKER_MODEL_ID = "morph-dsv41flash-abliterated"
# Landing src/lib/pricing.ts list prices, micro-USD per million tokens. Cached input is cheaper,
# so charging every prompt at the uncached rate remains conservative. K3.1 bills at K3's rates
# under a different id; Hack Me deliberately permits only the K3 id requested by the product.
MODEL_PRICES = {
    COORDINATOR_MODEL_ID: (2_500_000, 14_000_000),
    WORKER_MODEL_ID: (300_000, 1_200_000),
}
LIST_INPUT_MICROUSD_PER_MTOK = MODEL_PRICES[WORKER_MODEL_ID][0]
LIST_OUTPUT_MICROUSD_PER_MTOK = MODEL_PRICES[WORKER_MODEL_ID][1]
INPUT_PRICE_ENV = "HACK_ME_INPUT_PRICE_MICROUSD_PER_MTOK"
OUTPUT_PRICE_ENV = "HACK_ME_OUTPUT_PRICE_MICROUSD_PER_MTOK"
MODEL_PRICE_ENV = {
    WORKER_MODEL_ID: (INPUT_PRICE_ENV, OUTPUT_PRICE_ENV),
    COORDINATOR_MODEL_ID: (
        "HACK_ME_KIMI_INPUT_PRICE_MICROUSD_PER_MTOK",
        "HACK_ME_KIMI_OUTPUT_PRICE_MICROUSD_PER_MTOK",
    ),
}

# Fixed per-request token allowance for text the server adds around the serialized body: the chat
# template's role and special tokens, the BOS token, the tool-calling preamble and any
# gateway-injected system text. See input_token_upper_bound().
INPUT_TOKEN_OVERHEAD = 16_384

LEDGER_FILE = "inference-ledger.json"
LEDGER_VERSION = 1


class BudgetError(Exception):
    """The ledger refused an operation. Callers must not forward the request."""


class PricingError(BudgetError):
    pass


class LedgerCorrupt(BudgetError):
    pass


class BudgetExhausted(BudgetError):
    pass


class ReservationReplay(BudgetError):
    pass


class NotAdmitted(BudgetError):
    """The engagement stopped being authorized before the reservation could be committed."""


class SettlementConflict(BudgetError):
    pass


def _strict_int(value) -> bool:
    return type(value) is int


def prices(environ=os.environ, model_id=WORKER_MODEL_ID) -> tuple[int, int]:
    """Return one allowed model's prices. Overrides may only raise its Landing list price."""
    try:
        names = MODEL_PRICE_ENV[model_id]
        floors = MODEL_PRICES[model_id]
    except KeyError as exc:
        raise PricingError("model is not allowlisted") from exc
    result = []
    for name, floor in zip(names, floors):
        raw = environ.get(name)
        if raw is None or raw == "":
            result.append(floor)
            continue
        if not raw.isascii() or not raw.isdigit():
            raise PricingError(f"{name} must be a whole number of micro-USD per million tokens")
        value = int(raw)
        if value < floor:
            # A lower price would let an engagement spend past its cap. Refuse every paid call.
            raise PricingError(f"{name} may only raise the list price, never lower it")
        result.append(value)
    return result[0], result[1]


def charge_microusd(input_tokens: int, output_tokens: int, price: tuple[int, int]) -> int:
    """Integer cost, rounded up to the next micro-USD."""
    if not (_strict_int(input_tokens) and _strict_int(output_tokens)) or input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative integers")
    numerator = input_tokens * price[0] + output_tokens * price[1]
    return -(-numerator // 1_000_000)


def input_token_upper_bound(upstream_body: bytes) -> int:
    """Upper bound on prompt tokens for a text-only chat request.

    DeepSeek's tokenizer is a byte-level BPE: every token of ordinary text decodes to at least one
    UTF-8 byte, so a string never tokenizes to more tokens than it has bytes (a literal special-token
    string becomes one token, which only lowers the count). The broker serializes with ASCII
    escapes, so every character of content occupies at least as many body bytes as its UTF-8 form
    (\\uXXXX is 6 bytes for a 2-3 byte character, a surrogate pair is 12 bytes for 4), and every
    message also costs at least 28 bytes of JSON syntax ({"role":"user","content":""}) that the
    template replaces with a handful of role tokens. Tool schemas are rendered from JSON the body
    already contains. What the server adds that is not in the body (template preamble, BOS, tool
    instructions, injected system text) is covered by INPUT_TOKEN_OVERHEAD.

    This does NOT hold for images, audio, video or files: a 30-byte URL can expand to thousands of
    tokens. The broker therefore rejects every non-text content part before reserving.
    """
    return len(upstream_body) + INPUT_TOKEN_OVERHEAD


def ledger_path(store: EngagementStore, engagement_id: str) -> Path:
    return store._dir(engagement_id) / LEDGER_FILE


def _empty(cap: int) -> dict:
    return {
        "version": LEDGER_VERSION,
        "capMicroUsd": cap,
        "committedMicroUsd": 0,
        "requestCount": 0,
        "reservations": {},
    }


def check_invariant(ledger: dict) -> None:
    """Raise LedgerCorrupt unless the ledger satisfies every invariant."""
    try:
        if ledger.get("version") != LEDGER_VERSION:
            raise LedgerCorrupt("unknown ledger version")
        cap = ledger["capMicroUsd"]
        committed, count, reservations = ledger["committedMicroUsd"], ledger["requestCount"], ledger["reservations"]
        if not all(_strict_int(v) for v in (cap, committed, count)) or not isinstance(reservations, dict):
            raise LedgerCorrupt("ledger fields have the wrong type")
        if not 0 < cap <= HARD_CAP_MICROUSD:
            raise LedgerCorrupt("ledger limits exceed the hard limits")
        total = 0
        for entry in reservations.values():
            maximum, actual = entry.get("maximumMicroUsd"), entry.get("actualMicroUsd")
            if entry.get("model") not in (None, *MODEL_PRICES):
                raise LedgerCorrupt("reservation model is not allowlisted")
            if not _strict_int(maximum) or not 1 <= maximum <= cap:
                raise LedgerCorrupt("reservation maximum out of range")
            if actual is None:
                total += maximum
            elif _strict_int(actual) and 0 <= actual <= maximum:
                total += actual
            else:
                raise LedgerCorrupt("settled actual out of range")
        if committed != total or not 0 <= committed <= cap:
            raise LedgerCorrupt("committed spend does not equal unsettled maxima plus settled actuals")
        if count != len(reservations):
            raise LedgerCorrupt("request count does not match reservations")
    except (KeyError, TypeError, AttributeError) as exc:
        raise LedgerCorrupt("ledger is malformed") from exc


class InferenceLedger:
    """All reads and writes happen under the engagement's flock in EngagementStore."""

    def __init__(self, store: EngagementStore, cap_microusd: int = HARD_CAP_MICROUSD):
        if not _strict_int(cap_microusd) or not 0 < cap_microusd <= HARD_CAP_MICROUSD:
            raise ValueError("inference cap must be between 1 micro-USD and $1,000")
        self.store = store
        self.cap = cap_microusd

    def _load(self, engagement_id: str) -> dict:
        path = ledger_path(self.store, engagement_id)
        try:
            with path.open() as source:
                ledger = json.load(source)
        except FileNotFoundError:
            return _empty(self.cap)
        except (OSError, ValueError) as exc:
            raise LedgerCorrupt("ledger is unreadable") from exc
        check_invariant(ledger)
        return ledger

    def _save(self, engagement_id: str, ledger: dict) -> None:
        # Ledgers written before the request cap was removed carry a stale requestCap; drop it.
        ledger.pop("requestCap", None)
        check_invariant(ledger)
        EngagementStore._write(ledger_path(self.store, engagement_id), ledger)

    def snapshot(self, engagement_id: str) -> dict:
        with self.store._lock(engagement_id):
            return self._load(engagement_id)

    def reserve(self, engagement_id: str, request_id: str, maximum_microusd: int, now: float,
                guard=None, model=None) -> dict:
        """Atomically commit a request's maximum charge, or raise without changing anything.

        `guard` runs under the same flock that EngagementStore.request_cancel() and update() take,
        so a cancellation lands either before this reservation (refused) or after it (counted).
        """
        if not _strict_int(maximum_microusd) or maximum_microusd < 1:
            raise BudgetExhausted("request maximum must be a positive integer")
        with self.store._lock(engagement_id):
            if guard is not None and not guard():
                raise NotAdmitted("engagement is no longer authorized")
            ledger = self._load(engagement_id)
            if request_id in ledger["reservations"]:
                raise ReservationReplay("request id was already reserved")
            # A lower configured limit applies at once; a higher one never loosens a stored limit.
            cap = min(ledger["capMicroUsd"], self.cap)
            if maximum_microusd > cap - ledger["committedMicroUsd"]:
                raise BudgetExhausted("engagement inference budget exhausted")
            ledger["reservations"][request_id] = {
                "maximumMicroUsd": maximum_microusd,
                "actualMicroUsd": None,
                "reservedAtUnix": now,
                "settledAtUnix": None,
                "outcome": None,
                "model": model,
            }
            ledger["committedMicroUsd"] += maximum_microusd
            ledger["requestCount"] += 1
            self._save(engagement_id, ledger)
            return ledger

    def settle(self, engagement_id: str, request_id: str, actual_microusd: int, now: float,
               outcome: str) -> dict:
        """Replace a reservation's maximum with its actual charge, exactly once.

        An identical replay is a no-op; a different second value raises SettlementConflict.
        """
        with self.store._lock(engagement_id):
            ledger = self._load(engagement_id)
            entry = ledger["reservations"].get(request_id)
            if entry is None:
                raise SettlementConflict("no reservation for this request id")
            maximum = entry["maximumMicroUsd"]
            if not _strict_int(actual_microusd) or not 0 <= actual_microusd <= maximum:
                raise SettlementConflict("actual charge outside the reservation's bounds")
            if entry["actualMicroUsd"] is not None:
                if entry["actualMicroUsd"] != actual_microusd:
                    raise SettlementConflict("conflicting settlement")
                return ledger
            entry.update(actualMicroUsd=actual_microusd, settledAtUnix=now, outcome=outcome)
            ledger["committedMicroUsd"] += actual_microusd - maximum
            self._save(engagement_id, ledger)
            return ledger
