"""Deterministic parts: config substitution, pricing, answer normalisation, ranking, coverage
arithmetic, strict schemas, consistency metrics."""
from __future__ import annotations

import json
import math

import pytest
from pydantic import BaseModel

from po_agent.config import substitute
from po_agent.engines.base import choice, chunked, confidence, normalise, noul, score
from po_agent.engines.llm_decider import DecisionAnswers, OptionP, QuestionAnswer, render, to_raw
from po_agent.engines.openrouter_llm import strict_schema
from po_agent.evaluation.consistency import agreement, decisions
from po_agent.graph import questions as Q
from po_agent.pricing import Cost, Pricing, total
from po_agent.state import Requirement
from po_agent.telemetry import metric_attrs


def test_substitute_env(monkeypatch):
    monkeypatch.setenv("X", "val")
    monkeypatch.delenv("Y", raising=False)
    assert substitute({"a": "${X}", "b": "${Y:-dflt}", "c": ["${X}/${Y:-z}"], "d": 3}) == \
        {"a": "val", "b": "dflt", "c": ["val/z"], "d": 3}


def test_pricing_prefers_reported_then_catalogue_then_unknown():
    p = Pricing({"m": {"input": 1.0, "output": 5.0}})
    assert p.cost("m", 1000, 100, reported=0.01) == Cost(0.01, "reported")
    assert p.cost("m", 1_000_000, 100_000).value == pytest.approx(1.5)
    assert p.cost("other", 10, 10) == Cost(None, "unknown")
    assert total([Cost(1, "reported"), Cost(2, "reported")]) == Cost(3, "reported")
    assert total([Cost(1, "reported"), Cost(2, "catalogue")]).source == "mixed"
    assert not total([Cost(1, "reported"), Cost(None, "unknown")]).known


def test_confidence_is_one_minus_normalised_entropy():
    assert confidence([1.0, 0.0]) == 1.0
    assert confidence([0.5, 0.5]) == 0.0
    assert confidence([0.25] * 4) == 0.0
    assert 0 < confidence([0.7, 0.3]) < 1


def test_normalise_noul_choice_score_from_all_wire_shapes():
    a = normalise(noul("x"), {"noul": 0.96})
    assert a.noul == 0.96 and a.probabilities == {"yes": 0.96, "no": pytest.approx(0.04)}
    a = normalise(noul("x"), {"p_yes": 1.4})          # LLM shape, clipped
    assert a.noul == 1.0
    q = choice("x", {"web": "w", "app": "a"})
    a = normalise(q, {"probabilities": {"web": 0.2, "app": 0.8}, "confidence": 0.5})
    assert a.choice == "app" and a.confidence == 0.5
    a = normalise(q, {"probabilities": {"web": 3, "app": 1}})   # unnormalised -> renormalised
    assert a.choice == "web" and math.isclose(sum(a.probabilities.values()), 1)
    q = score("x", ["lo", "mid", "hi"])
    for raw in ({"probabilities": [0.1, 0.2, 0.7]}, {"probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}}):
        a = normalise(q, raw)
        assert a.score == pytest.approx(1.6) and a.legend["2"] == "hi"


def test_level_value_and_most_likely():
    assert Q.level_value({"0": 0, "1": 0, "2": 1, "3": 0, "4": 0}) == 3.0
    assert Q.level_value({"0": 0.5, "4": 0.5}) == 3.0


def test_chunking_keeps_order_and_covers_all():
    qs = {f"q{i}": noul("x") for i in range(25)}
    chunks = chunked(qs, 12)
    assert [len(c) for c in chunks] == [12, 12, 1]
    assert [k for c in chunks for k in c] == list(qs)
    assert chunked({}, 12) == [{}]


def test_rank_requirements_is_deterministic():
    def req(id, moscow, outs, risks):
        return Requirement(id=id, title=id, statement="s", rationale="r", persona_ids=[], outcome_ids=outs,
                           risk_ids=risks, acceptance_criteria=[], moscow=moscow)
    ranked = Q.rank_requirements([req("b", "should", ["o1"], []), req("a", "must", [], []),
                                  req("c", "must", ["o1", "o2"], ["r1"]), req("d", "must", ["o1", "o2"], [])])
    assert [r.id for r in ranked] == ["c", "d", "a", "b"]
    assert [r.rank for r in ranked] == [1, 2, 3, 4]


def test_strict_schema_requires_everything():
    class Inner(BaseModel):
        x: int
        y: str = "d"

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    s = strict_schema(Outer)
    assert s["required"] == ["inner", "items"] and s["additionalProperties"] is False
    assert s["$defs"]["Inner"]["required"] == ["x", "y"]


def test_llm_decider_schema_and_prompt_mirror_the_questions():
    qs = {"cap:a": noul("A?"), "k:b": choice("K?", {"x": "ex", "y": None}), "s:c": score("S?", ["l", "h"])}
    text = render({"t": 1}, qs)
    assert "[id: cap:a] type=noul" in text and "option x: ex" in text and "option 1: h" in text
    assert "Return exactly 3 answers" in text
    got = DecisionAnswers(answers=[
        QuestionAnswer(id="cap:a", p_yes=0.8, probabilities=[]),
        QuestionAnswer(id="k:b", p_yes=None, probabilities=[OptionP(option="x", p=0.3), OptionP(option="y", p=0.7)]),
        QuestionAnswer(id="s:c", p_yes=None, probabilities=[OptionP(option="0", p=0.25), OptionP(option="1", p=0.75)])])
    by = {a.id: a for a in got.answers}
    assert normalise(qs["cap:a"], to_raw(qs["cap:a"], by["cap:a"])).noul == 0.8
    assert normalise(qs["k:b"], to_raw(qs["k:b"], by["k:b"])).choice == "y"
    assert normalise(qs["s:c"], to_raw(qs["s:c"], by["s:c"])).score == 0.75
    assert normalise(qs["cap:a"], to_raw(qs["cap:a"], None)).noul == 0.5       # missing -> unsure
    # the schema is constant and small, whatever the batch
    assert len(json.dumps(strict_schema(DecisionAnswers))) < 1500


def test_metric_attrs_drop_high_cardinality():
    assert metric_attrs({"run.id": "abc", "workflow.variant": "llm", "graph.node.name": "x", "prompt": "..."}) == \
        {"workflow.variant": "llm", "graph.node.name": "x"}


def test_consistency_decisions_and_agreement():
    out = {"persona_selections": [{"persona": {"id": "p1"}, "p_impacted": 0.9, "selected": True, "impact_level": "high"},
                                  {"persona": {"id": "p2"}, "p_impacted": 0.2, "selected": False, "impact_level": "none"}],
           "evidence": {"answers": {}}}
    d = decisions(out)
    assert d == {"persona_selections[p1].persona.id": "p1", "persona_selections[p1].selected": True,
                 "persona_selections[p1].impact_level": "high", "persona_selections[p2].persona.id": "p2",
                 "persona_selections[p2].selected": False, "persona_selections[p2].impact_level": "none"}
    same = [{"a": True, "b": "x"}] * 4
    assert agreement(same)["identical_rate"] == 1.0 and agreement(same)["decision_agreement"] == 1.0
    mixed = [{"a": True, "b": "x"}, {"a": True, "b": "y"}, {"a": True, "b": "x"}, {"a": False, "b": "x"}]
    r = agreement(mixed)
    assert r["identical_rate"] == 0.5 and r["decision_agreement"] == pytest.approx((0.75 + 0.75) / 2)
    assert {u["decision"] for u in r["unstable"]} == {"a", "b"}
