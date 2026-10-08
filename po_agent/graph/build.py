"""Assemble the LangGraph: nodes, edges, three gated loops with caps."""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..config import Loops
from ..state import ProductOwnerState
from .nodes import Nodes, RunContext


def after_assess_risks(loops: Loops):
    def route(state: ProductOwnerState) -> str:
        flagged = any(r.needs_investigation for r in state.get("risks", []))
        return "investigate" if flagged and state.get("risk_attempt", 1) < loops.risk_attempts else "proceed"
    return route


def after_check_coverage(loops: Loops):
    def route(state: ProductOwnerState) -> str:
        insufficient = not state["coverage"].sufficient
        return "refine" if insufficient and state.get("requirement_attempt", 1) < loops.requirement_attempts else "proceed"
    return route


def after_validate_prd(loops: Loops):
    def route(state: ProductOwnerState) -> str:
        failed = not state["validation"].passed
        return "refine" if failed and state.get("prd_attempt", 1) < loops.prd_attempts else "proceed"
    return route


def build_graph(ctx: RunContext, nodes: Nodes | None = None):
    nodes = nodes or Nodes(ctx)
    loops = ctx.settings.loops
    g = StateGraph(ProductOwnerState)
    for spec in nodes.specs.values():
        g.add_node(spec.name, nodes.wrap(spec))

    g.add_edge(START, "parse_intent")
    g.add_edge("parse_intent", "retrieve_knowledge")
    g.add_edge("retrieve_knowledge", "select_capabilities")
    g.add_edge("select_capabilities", "select_personas")
    g.add_edge("select_personas", "identify_outcomes")
    g.add_edge("identify_outcomes", "retrieve_constraints")
    g.add_edge("retrieve_constraints", "assess_constraints")
    g.add_edge("assess_constraints", "discover_risks")
    g.add_edge("discover_risks", "assess_risks")
    g.add_conditional_edges("assess_risks", after_assess_risks(loops),
                            {"investigate": "investigate_risks", "proceed": "generate_requirements"})
    g.add_edge("investigate_risks", "assess_risks")
    g.add_edge("generate_requirements", "classify_requirements")
    g.add_edge("classify_requirements", "check_coverage")
    g.add_conditional_edges("check_coverage", after_check_coverage(loops),
                            {"refine": "refine_requirements", "proceed": "construct_prd"})
    g.add_edge("refine_requirements", "classify_requirements")
    g.add_edge("construct_prd", "validate_prd")
    g.add_conditional_edges("validate_prd", after_validate_prd(loops),
                            {"refine": "refine_prd", "proceed": "finalise"})
    g.add_edge("refine_prd", "validate_prd")
    g.add_edge("finalise", END)
    return g.compile()


def _doc_context() -> RunContext:
    """A context with no live engines, enough to compile the graph for documentation."""
    from unittest.mock import MagicMock

    from ..config import Settings
    settings = Settings(llm={"model": "x"}, jev={}, judge={"model": "x"})
    return RunContext(settings=settings, llm=MagicMock(), decider=MagicMock(engine_type="jev", model="jev"),
                      knowledge=MagicMock(), telemetry=MagicMock(), variant="hybrid", experiment_id="",
                      run_id="")


def mermaid() -> str:
    """The graph as Mermaid, from the compiled graph itself, so docs cannot drift from code."""
    return build_graph(_doc_context()).get_graph().draw_mermaid()


def graph_spec() -> dict:
    """The graph as data for the docs site (docs/data/graph.json): every node with its type and
    the state it hashes as input, every edge with its branch name when conditional, and the loop
    caps. Written by `po-agent graph --json`; a test keeps the checked-in copy equal to this."""
    ctx = _doc_context()
    nodes = Nodes(ctx)
    g = build_graph(ctx, nodes).get_graph()
    return {
        "nodes": [{"name": s.name, "type": s.node_type, "inputs": list(s.inputs), "attempt_key": s.attempt_key}
                  for s in nodes.specs.values()],
        "edges": [{"source": e.source, "target": e.target, "branch": e.data if e.conditional else None}
                  for e in g.edges],
        "loops": ctx.settings.loops.model_dump(),
    }
