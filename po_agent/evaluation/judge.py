"""Blind A/B judging of two PRDs against the rubric. The judge sees the intent, PRD A, PRD B and
the rubric; it never sees which variant produced which. The A/B mapping is decided here, stored
with the raw judgement, and only applied afterwards.
"""
from __future__ import annotations

import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .. import telemetry as T
from ..config import Settings
from ..engines import OpenRouterLLM
from ..pricing import Pricing

RUBRIC = (Path(__file__).parent / "rubric.md").read_text()
DIMENSIONS = ["intent_fidelity", "requirement_coverage", "persona_coverage", "business_outcome_coverage",
              "risk_coverage", "constraint_compliance", "internal_consistency", "unsupported_assumptions",
              "actionability", "prd_completeness"]


class DimensionScore(BaseModel):
    dimension: str
    a: int = Field(ge=1, le=5)
    b: int = Field(ge=1, le=5)
    note: str


class Judgement(BaseModel):
    scores: list[DimensionScore]
    overall_preference: Literal["A", "B", "tie"]
    justification: str


SYSTEM = ("You are an exacting reviewer of product requirements documents. You compare two PRDs "
          "written from the same intent. You do not know who wrote them. Score strictly and "
          "independently per dimension, and explain briefly.")


def _prompt(intent: str, a: str, b: str) -> str:
    return (f"RUBRIC:\n{RUBRIC}\n\nINTENT:\n{intent}\n\n===== PRD A =====\n{a}\n\n===== PRD B =====\n{b}\n\n"
            "Return one score entry per rubric dimension, in the rubric's order, with the exact dimension "
            "names and a one-sentence note each, then your overall preference with a short justification.")


def _strip_front_matter(prd: str) -> str:
    if prd.startswith("---"):
        end = prd.find("\n---", 3)
        if end != -1:
            return prd[end + 4:].lstrip()
    return prd


async def judge_pair(settings: Settings, pricing: Pricing, telemetry: T.Telemetry, intent: str,
                     prds: dict[str, str], order: tuple[str, str] | None = None,
                     rng: random.Random | None = None) -> dict:
    """prds: {variant: prd}. Returns the raw judgement plus the mapping, and unblinded scores."""
    rng = rng or random.Random()
    variants = list(prds)
    order = order or tuple(rng.sample(variants, 2))
    mapping = {"A": order[0], "B": order[1]}
    llm = OpenRouterLLM(settings.llm, pricing, telemetry)
    try:
        with telemetry.span("evaluation.judge", {T.OPERATION: "judge", T.MODEL_NAME: settings.judge.model}):
            r = await llm.generate(system=SYSTEM, schema=Judgement, purpose="judge",
                                   prompt=_prompt(intent, _strip_front_matter(prds[order[0]]),
                                                  _strip_front_matter(prds[order[1]])),
                                   model=settings.judge.model, temperature=settings.judge.temperature,
                                   max_tokens=settings.judge.max_tokens,
                                   # Reasoning tokens are billed as output and can exhaust the budget
                                   # before the JSON starts; the rubric does not need them.
                                   extra={"reasoning": {"enabled": False}})
    finally:
        await llm.aclose()
    j: Judgement = r.data
    by_variant = {v: {} for v in variants}
    for s in j.scores:
        by_variant[mapping["A"]][s.dimension] = s.a
        by_variant[mapping["B"]][s.dimension] = s.b
    preferred = mapping.get(j.overall_preference, "tie")
    return {"mapping": mapping, "judge_model": r.model, "raw": j.model_dump(),
            "scores": by_variant, "preferred": preferred,
            "totals": {v: sum(sc.values()) for v, sc in by_variant.items()},
            "usage": {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
                      "cost": r.usage.cost.as_dict()}}
