"""The pricing catalogue (config/pricing.yaml) and the one cost function used everywhere.

A cost is a `Cost(value, source)`. `source` says where the number came from: `reported` (the
provider sent it), `catalogue` (computed from tokens x list price), or `unknown` (neither; value
is None). Reports never sum an unknown cost silently: `total()` returns unknown if any part is.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Cost:
    value: float | None
    source: str  # reported | catalogue | unknown

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict:
        return {"usd": self.value, "source": self.source}


UNKNOWN = Cost(None, "unknown")


def total(costs: Iterable[Cost]) -> Cost:
    costs = list(costs)
    if not costs:
        return Cost(0.0, "catalogue")
    if any(not c.known for c in costs):
        return UNKNOWN
    sources = {c.source for c in costs}
    return Cost(sum(c.value for c in costs), sources.pop() if len(sources) == 1 else "mixed")


class Pricing:
    def __init__(self, models: dict[str, dict]):
        self.models = models

    @classmethod
    def load(cls, path: str | Path) -> Pricing:
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls(data.get("models") or {})

    def has(self, model: str) -> bool:
        return model in self.models

    def catalogue_cost(self, model: str, input_tokens: int, output_tokens: int) -> Cost:
        price = self.models.get(model)
        if not price:
            return UNKNOWN
        usd = (input_tokens * float(price["input"]) + output_tokens * float(price["output"])) / 1e6
        return Cost(usd, "catalogue")

    def cost(self, model: str, input_tokens: int, output_tokens: int,
             reported: float | None = None) -> Cost:
        """Prefer what the provider reported; otherwise compute from the catalogue."""
        if reported is not None:
            return Cost(float(reported), "reported")
        return self.catalogue_cost(model, input_tokens, output_tokens)

    def snapshot(self) -> dict:
        return {"models": self.models}
