"""Model prices and the cost of an agent call.

Prices are US dollars per million tokens, kept in the ``model_prices`` table.
A price's ``model`` is an exact model name or a ``*`` pattern, so one row can
cover dated snapshots (``gpt-4.1-mini*`` matches ``gpt-4.1-mini-2025-04-14``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fnmatch import fnmatchcase
from typing import Any

from app.runtimes.base import TokenUsage

MILLION = Decimal(1_000_000)
COST_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class Price:
    model: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal

    def cost(self, usage: TokenUsage) -> Decimal:
        total = (
            Decimal(usage.input_tokens) * self.input_per_mtok
            + Decimal(usage.output_tokens) * self.output_per_mtok
        ) / MILLION
        return total.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


class PriceBook:
    """Looks up the price of a model: an exact name first, then the longest
    matching pattern."""

    def __init__(self, prices: list[Price] | None = None) -> None:
        self._exact: dict[str, Price] = {}
        self._patterns: list[Price] = []
        for price in prices or []:
            if "*" in price.model or "?" in price.model:
                self._patterns.append(price)
            else:
                self._exact[price.model] = price
        self._patterns.sort(key=lambda p: len(p.model.replace("*", "")), reverse=True)

    def find(self, model: str | None) -> Price | None:
        if not model:
            return None
        if model in self._exact:
            return self._exact[model]
        return next((p for p in self._patterns if fnmatchcase(model, p.model)), None)

    def cost(self, model: str | None, usage: TokenUsage | dict[str, Any] | None) -> Decimal | None:
        """Dollar cost of a call, or None when the model has no price."""
        if usage is None:
            return None
        price = self.find(model)
        if price is None:
            return None
        if isinstance(usage, dict):
            usage = TokenUsage.model_validate(usage)
        return price.cost(usage)


async def load_price_book(session: Any) -> PriceBook:
    from sqlalchemy import select

    from app.models import ModelPrice

    rows = await session.scalars(select(ModelPrice))
    return PriceBook([Price(r.model, r.input_per_mtok, r.output_per_mtok) for r in rows])
