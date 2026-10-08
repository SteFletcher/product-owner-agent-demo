"""Engine protocols, result types, the System One question builders, and answer normalisation.

A DecisionEngine takes one `state` (text or JSON-able) and a dict of questions in Jev's wire
shape, and returns one `Answer` per question in a normalised shape: probabilities always keyed by
option (or level index as a string), `choice`/`score` derived, `confidence = 1 - H(p)/ln K`.
Both the Jev engine and the LLM decision engine return exactly this, so nodes and the
consistency experiment never see which engine answered.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from ..pricing import Cost
from ..state import Answer


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Cost = field(default_factory=lambda: Cost(None, "unknown"))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResult:
    text: str
    data: BaseModel | None
    model: str
    provider: str | None
    usage: Usage
    latency_ms: float
    retries: int
    request_id: str | None = None


@dataclass
class DecisionResult:
    answers: dict[str, Answer]
    model: str
    provider: str | None
    usage: Usage
    latency_ms: float
    retries: int
    calls: int = 1
    request_id: str | None = None


class LLMEngine(Protocol):
    engine_type: str      # "llm"
    provider_name: str

    async def generate(self, *, system: str, prompt: str, schema: type[BaseModel] | None,
                       purpose: str, model: str | None = None, temperature: float | None = None,
                       max_tokens: int | None = None, extra: dict[str, Any] | None = None) -> LLMResult: ...


class DecisionEngine(Protocol):
    engine_type: str      # "jev" | "llm"
    provider_name: str
    model: str
    chunk_size: int

    async def decide(self, *, state: Any, questions: dict[str, dict], purpose: str) -> DecisionResult: ...


# --- question builders (Jev's wire shape) --------------------------------------------------------


def noul(instructions: str, true: str | None = None, false: str | None = None) -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true, "false": false}
    return q


def choice(instructions: str, options: dict[str, str | None]) -> dict:
    if not 2 <= len(options) <= 20:
        raise ValueError("a choice needs 2 to 20 options")
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions: str, levels: list[str]) -> dict:
    if not 2 <= len(levels) <= 10:
        raise ValueError("a score needs 2 to 10 levels")
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


# --- answer normalisation ------------------------------------------------------------------------


def confidence(p: list[float]) -> float:
    k = len(p)
    if k < 2:
        return 1.0
    h = -sum(x * math.log(x) for x in p if x > 0)
    return round(max(0.0, min(1.0, 1 - h / math.log(k))), 4)


def _renorm(p: dict[str, float]) -> dict[str, float]:
    clipped = {k: max(0.0, float(v)) for k, v in p.items()}
    s = sum(clipped.values())
    if s <= 0:
        return {k: 1 / len(clipped) for k in clipped}
    return {k: v / s for k, v in clipped.items()}


def normalise(question: dict, raw: dict) -> Answer:
    """Turn a wire answer (Jev's, OpenRouter's, the local server's or the LLM's) into an Answer."""
    kind = question["type"]
    if kind == "noul":
        p = float(raw.get("noul", raw.get("p_yes", 0.5)))
        p = max(0.0, min(1.0, p))
        return Answer(type="noul", noul=p, probabilities={"yes": p, "no": 1 - p},
                      confidence=float(raw.get("confidence", confidence([p, 1 - p]))))
    if kind == "choice":
        keys = list(question["criteria"])
        probs = raw.get("probabilities") or {}
        p = _renorm({k: probs.get(k, 0.0) for k in keys})
        best = max(keys, key=p.__getitem__)
        return Answer(type="choice", choice=best, probabilities=p,
                      confidence=float(raw.get("confidence", confidence(list(p.values())))))
    levels = question["criteria"]
    probs = raw.get("probabilities")
    if isinstance(probs, list):
        probs = {str(i): v for i, v in enumerate(probs)}
    probs = probs or {}
    p = _renorm({str(i): float(probs.get(str(i), 0.0)) for i in range(len(levels))})
    expected = sum(int(i) * v for i, v in p.items())
    return Answer(type="score", score=round(expected, 4), probabilities=p,
                  legend={str(i): lv for i, lv in enumerate(levels)},
                  confidence=float(raw.get("confidence", confidence(list(p.values())))))


def most_likely_level(answer: Answer) -> str:
    best = max(answer.probabilities, key=answer.probabilities.get)
    return answer.legend.get(best, best)


def chunked(items: dict[str, dict], size: int) -> list[dict[str, dict]]:
    keys = list(items)
    return [{k: items[k] for k in keys[i:i + size]} for i in range(0, len(keys), max(1, size))] or [{}]
