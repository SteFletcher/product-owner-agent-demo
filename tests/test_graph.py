"""Graph routing through the compiled LangGraph with fake engines: the happy path, each of the
three loops firing and respecting its cap, the record every node leaves, and the variant
equivalence (same questions asked whichever decider answers)."""
from __future__ import annotations

import pytest

from po_agent.graph.build import build_graph
from po_agent.graph.nodes import Nodes
from po_agent.runner import run_variant, summarise
from tests.conftest import FakeDecider, FakeLLM, make_ctx, make_settings

INTENT = "Let customers reschedule a home delivery from the tracking page."


async def run(ctx):
    final = await build_graph(ctx, Nodes(ctx)).ainvoke(
        {"intent_text": INTENT, "node_results": [], "errors": []}, config={"recursion_limit": 60})
    return final, [r.node for r in final["node_results"]]


async def test_happy_path_visits_every_node_once(settings, telemetry):
    ctx = make_ctx(settings, telemetry, decider=FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9}))
    final, path = await run(ctx)
    assert path == ["parse_intent", "retrieve_knowledge", "select_capabilities", "select_personas",
                    "identify_outcomes", "retrieve_constraints", "assess_constraints", "discover_risks",
                    "assess_risks", "generate_requirements", "classify_requirements", "check_coverage",
                    "construct_prd", "validate_prd", "finalise"]
    assert final["prd_final"].startswith("---\nvariant: hybrid")
    assert final["coverage"].sufficient and final["validation"].passed
    assert all(r.status == "ok" for r in final["node_results"])
    assert {r.engine for r in final["node_results"]} == {"llm", "jev", "mcp", "none"}
    bounded = [r for r in final["node_results"] if r.node_type == "bounded"]
    assert all(r.engine == "jev" and r.output["evidence"]["decisions"] > 0 for r in bounded)


async def test_risk_loop_fires_once_then_proceeds(settings, telemetry):
    # Every risk flagged -> investigate -> reassess; mitigation present stops a second loop.
    ctx = make_ctx(settings, telemetry, decider=FakeDecider({r"^inv:": 0.95}))
    final, path = await run(ctx)
    assert path.count("assess_risks") == 2 and path.count("investigate_risks") == 1
    assert path.index("investigate_risks") == path.index("assess_risks") + 1
    assert all(r.mitigation == "Mitigated" for r in final["risks"])
    assert final["risk_attempt"] == 2
    attempts = [r.attempt for r in final["node_results"] if r.node == "assess_risks"]
    assert attempts == [1, 2]


async def test_risk_loop_respects_cap_when_priority_stays_high(telemetry):
    settings = make_settings(loops={"risk_attempts": 2})
    # likelihood 5 x impact 5 = 25 >= 12 every time, but mitigation exists after round 1 -> no third round
    ctx = make_ctx(settings, telemetry, decider=FakeDecider({r"^(lik|imp):": 4, r"^inv:": 0.99}))
    _, path = await run(ctx)
    assert path.count("assess_risks") == 2


async def test_coverage_loop_refines_until_cap(telemetry):
    settings = make_settings(loops={"requirement_attempts": 3})
    # Requirements never link personas/outcomes and the decider says outcomes are not delivered.
    ctx = make_ctx(settings, telemetry, llm=FakeLLM(link_everything=False),
                   decider=FakeDecider({r"^(del|goal):": 0.1, r"^inv:": 0.1}))
    final, path = await run(ctx)
    assert path.count("classify_requirements") == 3 and path.count("refine_requirements") == 2
    assert not final["coverage"].sufficient and final["requirement_attempt"] == 3
    assert "construct_prd" in path     # the cap lets the workflow finish with gaps recorded


async def test_prd_validation_loop(telemetry):
    settings = make_settings(loops={"prd_attempts": 2})
    ctx = make_ctx(settings, telemetry,
                   decider=FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9, r"^requirements_complete$": 0.2}))
    final, path = await run(ctx)
    assert path.count("validate_prd") == 2 and path.count("refine_prd") == 1
    assert not final["validation"].passed and "requirements_complete" in final["validation"].failed
    assert final["prd_final"].split("\n")[6] == "validation_passed: False"


async def test_non_blocking_check_failure_does_not_loop(settings, telemetry):
    ctx = make_ctx(settings, telemetry, decider=FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9, r"^actionable$": 0.1}))
    final, path = await run(ctx)
    assert path.count("validate_prd") == 1 and final["validation"].passed
    assert final["validation"].failed == ["actionable"]


async def test_both_variants_ask_identical_questions(settings, telemetry):
    """The decider sees the same question ids and texts whichever engine it is."""
    seen = {}
    for variant in ("llm", "hybrid"):
        dec = FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9})
        dec.engine_type = "llm" if variant == "llm" else "jev"
        asked = []
        original = dec.decide

        async def spy(asked=asked, original=original, **kw):
            asked.append((kw["purpose"], tuple(sorted(kw["questions"])), kw["state"]))
            return await original(**kw)
        dec.decide = spy
        ctx = make_ctx(settings, telemetry, decider=dec, variant=variant)
        await run(ctx)
        seen[variant] = asked
    assert seen["llm"] == seen["hybrid"]
    assert [p for p, _, _ in seen["llm"]] == ["select_capabilities", "select_personas", "assess_constraints",
                                              "assess_risks", "classify_requirements", "check_coverage",
                                              "validate_prd"]


async def test_run_variant_records_failure_as_data(settings, telemetry, pricing, tmp_path, monkeypatch):
    class Boom(FakeLLM):
        async def generate(self, **kw):
            if kw["purpose"] == "discover_risks":
                raise RuntimeError("provider down")
            return await super().generate(**kw)

    from po_agent import runner
    monkeypatch.setattr(runner, "make_engines", lambda s, v, p, t: (Boom(), FakeDecider()))
    from po_agent.knowledge import StaticKnowledge
    rec = await run_variant(settings, "hybrid", INTENT, "exp", "hybrid-01", pricing, telemetry,
                            out_dir=tmp_path / "hybrid", knowledge=StaticKnowledge(telemetry))
    assert not rec.ok and "provider down" in rec.error
    failed = [r for r in rec.node_results if r.status == "error"]
    assert failed and failed[0].node == "discover_risks"
    m = rec.metrics()
    assert m["ok"] is False and m["errors"] == 1
    assert (tmp_path / "hybrid" / "metrics.json").exists()


async def test_summary_costs_stay_unknown_when_any_part_is(settings, telemetry):
    ctx = make_ctx(settings, telemetry, decider=FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9}))
    final, _ = await run(ctx)
    results = final["node_results"]
    assert summarise("hybrid", "r", True, 10.0, results, None, None)["total_cost"]["source"] == "catalogue"
    results[0] = results[0].model_copy(update={"cost_usd": None, "cost_source": "unknown"})
    assert summarise("hybrid", "r", True, 10.0, results, None, None)["total_cost"] == {"usd": None, "source": "unknown"}


@pytest.mark.parametrize("variant", ["llm", "hybrid"])
async def test_adk_agent_wraps_the_graph(settings, telemetry, pricing, monkeypatch, tmp_path, variant):
    from po_agent import runner
    monkeypatch.setattr(runner, "make_engines", lambda s, v, p, t: (FakeLLM(), FakeDecider({r"^inv:": 0.1, r"^goal:": 0.9})))
    monkeypatch.setattr(settings.knowledge, "transport", "inprocess")
    from po_agent.adk_app import run_via_adk
    record, meta = await run_via_adk(settings, pricing, telemetry, variant, INTENT, tmp_path)
    assert record.ok and meta["ok"] and meta["run_id"] == f"{variant}-01"
    assert (tmp_path / variant / "run-01" / "prd.md").read_text().startswith("---")
