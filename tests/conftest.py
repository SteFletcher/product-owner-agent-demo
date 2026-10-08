"""Offline fakes: an LLM that returns schema-shaped data, a decider with scripted probabilities,
in-process knowledge, and telemetry with exporters off. No network in any test."""
from __future__ import annotations

import json
import os
import re
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel

os.environ["PO_TELEMETRY"] = "0"

from po_agent.config import Settings
from po_agent.engines.base import DecisionResult, LLMResult, Usage, normalise
from po_agent.graph.nodes import RunContext
from po_agent.knowledge import StaticKnowledge
from po_agent.pricing import Cost, Pricing
from po_agent.state import (
    Brief,
    MitigationList,
    OutcomeList,
    PRDText,
    RequirementList,
    RiskList,
)
from po_agent.telemetry import Telemetry


def make_settings(**over) -> Settings:
    base = {"llm": {"model": "fake/llm", "api_key_env": ""}, "jev": {"model": "fake/jev", "api_key_env": ""},
            "judge": {"model": "fake/judge"}, "knowledge": {"transport": "inprocess"},
            "telemetry": {"enabled": False}}
    base.update(over)
    return Settings(**base)


class FakeLLM:
    """Returns a valid instance of the requested schema, derived from the prompt where it matters."""
    engine_type = "llm"
    provider_name = "fake"

    def __init__(self, n_requirements: int = 3, link_everything: bool = True):
        self.calls: list[str] = []
        self.n_requirements = n_requirements
        self.link_everything = link_everything

    async def generate(self, *, system, prompt, schema: type[BaseModel] | None, purpose, model=None,
                       temperature=None, max_tokens=None, extra=None) -> LLMResult:
        self.calls.append(purpose)
        data = self._build(schema, prompt) if schema else None
        text = "" if schema else "# Reschedule delivery\n\n## Requirements\n\n- req-0\n"
        return LLMResult(text=text, data=data, model="fake/llm", provider="fake",
                         usage=Usage(100, 50, Cost(0.001, "catalogue")), latency_ms=5.0, retries=0)

    def _build(self, schema, prompt) -> BaseModel:
        persona_ids = re.findall(r'"id": "(per-[a-z-]+)"', prompt)
        outcome_ids = re.findall(r'"id": "(out-[a-z0-9-]+)"', prompt)
        risk_ids = re.findall(r'"id": "(risk-[a-z0-9-]+)"', prompt)
        if schema is Brief:
            return Brief(title="Reschedule delivery", summary="Customers reschedule from the tracking page.",
                         goals=["Fewer calls", "Fewer failed deliveries"], in_scope=["day change"],
                         out_of_scope=["address change"], actors_mentioned=["customer", "agent"],
                         stated_assumptions=["carriers vary"], success_signals=["calls halve"])
        if schema is OutcomeList:
            return OutcomeList(outcomes=[
                {"id": "out-self-serve", "kind": "user", "statement": "Customers reschedule alone",
                 "measure": "share of reschedules self-served", "persona_ids": persona_ids[:1]},
                {"id": "out-fewer-calls", "kind": "business", "statement": "Fewer timing calls",
                 "measure": "calls per 1000 orders", "persona_ids": []}])
        if schema is RiskList:
            return RiskList(risks=[
                {"id": "risk-carrier-cutoff", "title": "Carrier cut-off", "description": "Change after cut-off fails.",
                 "affected_persona_ids": persona_ids[:1]},
                {"id": "risk-link-abuse", "title": "Tracking link abuse", "description": "Link holder changes delivery.",
                 "affected_persona_ids": []}])
        if schema is MitigationList:
            ids = re.findall(r'"id": "(risk-[a-z0-9-]+)"', prompt.split("FLAGGED RISKS:")[-1])
            return MitigationList(mitigations=[{"risk_id": i, "revised_description": f"{i} revised",
                                                "mitigation": "Mitigated", "residual_concern": "none"} for i in ids])
        if schema is RequirementList:
            n = self.n_requirements
            reqs = []
            for i in range(n):
                reqs.append({"id": f"req-{i}", "title": f"Requirement {i}", "statement": f"The system shall do {i}.",
                             "rationale": "because", "persona_ids": persona_ids if self.link_everything else [],
                             "outcome_ids": outcome_ids if self.link_everything else [],
                             "risk_ids": risk_ids[:1], "acceptance_criteria": ["Given/When/Then"]})
            return RequirementList(requirements=reqs)
        if schema is PRDText:
            return PRDText(markdown="# Reschedule delivery\n\n## Requirements\n\n- req-0\n")
        raise AssertionError(f"FakeLLM has no builder for {schema}")


class FakeDecider:
    """Scripted probabilities: a dict of regex (on the question id) -> value; default p_yes 0.9,
    first option for choices, level 2 for scores. `calls` records (purpose, n_questions)."""
    engine_type = "jev"
    provider_name = "fake"
    model = "fake/jev"
    chunk_size = 12

    # Without these, "yes to everything" marks every requirement a duplicate and flags every risk.
    DEFAULT_RULES: ClassVar[dict[str, float]] = {r"^dup:": 0.1, r"^inv:": 0.1}

    def __init__(self, rules: dict[str, Any] | None = None, default_yes: float = 0.9):
        self.rules = {**(rules or {}), **{k: v for k, v in self.DEFAULT_RULES.items() if k not in (rules or {})}}
        self.default_yes = default_yes
        self.calls: list[tuple[str, int]] = []

    def _value(self, qid: str, q: dict):
        for rx, v in self.rules.items():
            if re.search(rx, qid):
                return v
        return None

    async def decide(self, *, state, questions, purpose) -> DecisionResult:
        self.calls.append((purpose, len(questions)))
        json.dumps(state)          # a real engine serialises the state; a model object here is a bug
        json.dumps(questions)
        answers = {}
        for qid, q in questions.items():
            v = self._value(qid, q)
            if q["type"] == "noul":
                raw = {"noul": self.default_yes if v is None else v}
            elif q["type"] == "choice":
                keys = list(q["criteria"])
                pick = v if v in keys else keys[0]
                raw = {"probabilities": {k: (0.9 if k == pick else 0.1 / (len(keys) - 1)) for k in keys}}
            else:
                n = len(q["criteria"])
                lvl = min(n - 1, 2 if v is None else int(v))
                raw = {"probabilities": [0.9 if i == lvl else 0.1 / (n - 1) for i in range(n)]}
            answers[qid] = normalise(q, raw)
        return DecisionResult(answers=answers, model=self.model, provider="fake",
                              usage=Usage(len(questions) * 30, 0, Cost(0.00001, "catalogue")),
                              latency_ms=1.0, retries=0, calls=1)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def telemetry() -> Telemetry:
    return Telemetry(make_settings().telemetry)


@pytest.fixture
def pricing() -> Pricing:
    return Pricing({"fake/llm": {"input": 1.0, "output": 5.0}, "fake/jev": {"input": 0.042, "output": 0}})


def make_ctx(settings, telemetry, llm=None, decider=None, variant="hybrid") -> RunContext:
    return RunContext(settings=settings, llm=llm or FakeLLM(), decider=decider or FakeDecider(),
                      knowledge=StaticKnowledge(telemetry), telemetry=telemetry, variant=variant,
                      experiment_id="test", run_id="test-01")
