"""Run one variant once: build the engines for the variant, open knowledge, run the graph under a
root span, and write prd.md, metrics.json and node-results.json into the run directory.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import telemetry as T
from .config import Settings
from .engines import JevEngine, LLMDecisionEngine, OpenRouterLLM
from .graph.build import build_graph
from .graph.nodes import NodeFailed, Nodes, RunContext, _plain
from .knowledge import open_knowledge
from .pricing import Cost, Pricing, total
from .state import NodeResult, ProductOwnerState

VARIANTS = ("llm", "hybrid")


def new_experiment_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


@dataclass
class RunRecord:
    variant: str
    run_id: str
    ok: bool
    duration_ms: float
    prd: str
    node_results: list[NodeResult]
    trace_id: str | None
    error: str | None
    final_state: dict[str, Any]

    def metrics(self) -> dict[str, Any]:
        return summarise(self.variant, self.run_id, self.ok, self.duration_ms, self.node_results,
                         self.trace_id, self.error)


def make_engines(settings: Settings, variant: str, pricing: Pricing, telemetry: T.Telemetry):
    llm = OpenRouterLLM(settings.llm, pricing, telemetry)
    if variant == "hybrid":
        decider = JevEngine(settings.jev, pricing, telemetry)
    elif variant == "llm":
        decider = LLMDecisionEngine(llm, settings.llm, settings.jev)
    else:
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    return llm, decider


async def run_variant(settings: Settings, variant: str, intent_text: str, experiment_id: str,
                      run_id: str, pricing: Pricing, telemetry: T.Telemetry,
                      out_dir: Path | None = None, knowledge=None) -> RunRecord:
    llm, decider = make_engines(settings, variant, pricing, telemetry)
    attrs = {T.EXPERIMENT_ID: experiment_id, T.RUN_ID: run_id, T.VARIANT: variant,
             T.MODEL_NAME: settings.llm.model, "decision.engine": decider.engine_type,
             "decision.model": decider.model}
    started = time.perf_counter()
    results: list[NodeResult] = []
    prd, error, final, trace_id = "", None, {}, None
    try:
        with telemetry.span("ProductOwner.Run", attrs) as span:
            trace_id = T.current_trace_ids().get("trace_id")
            async with _knowledge(settings, telemetry, knowledge) as k:
                ctx = RunContext(settings=settings, llm=llm, decider=decider, knowledge=k,
                                 telemetry=telemetry, variant=variant, experiment_id=experiment_id,
                                 run_id=run_id)
                graph = build_graph(ctx, Nodes(ctx))
                state: ProductOwnerState = {"intent_text": intent_text, "node_results": [], "errors": []}
                final = dict(state)
                try:
                    # Stream the state so that a failure half-way keeps every node result so far.
                    async for final in graph.astream(state, config={"recursion_limit": 60},
                                                     stream_mode="values"):
                        pass
                except NodeFailed as e:
                    results = list(final.get("node_results", [])) + [e.record]
                    raise
                results = list(final.get("node_results", []))
                prd = final.get("prd_final", "")
                span.set_attributes({"prd.chars": len(prd), "nodes.executed": len(results)})
            ok = True
    except Exception as e:  # noqa: BLE001 - a failed run is a data point
        ok, error = False, f"{type(e).__name__}: {e}"
    finally:
        for engine in (llm, decider):
            if hasattr(engine, "aclose"):
                await engine.aclose()
    duration = (time.perf_counter() - started) * 1000
    telemetry.record_run(attrs, duration, ok)
    record = RunRecord(variant, run_id, ok, duration, prd, results, trace_id, error, final)
    if out_dir:
        write_run(out_dir, record)
    return record


def _knowledge(settings: Settings, telemetry: T.Telemetry, knowledge):
    import contextlib
    if knowledge is not None:
        @contextlib.asynccontextmanager
        async def given():
            yield knowledge
        return given()
    return open_knowledge(settings.knowledge, telemetry)


def write_run(out_dir: Path, record: RunRecord) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prd.md").write_text(record.prd)
    (out_dir / "metrics.json").write_text(json.dumps(record.metrics(), indent=1))
    (out_dir / "node-results.json").write_text(json.dumps([r.model_dump() for r in record.node_results], indent=1))
    if record.trace_id:
        (out_dir / "trace-id").write_text(record.trace_id + "\n")
    state = {k: v for k, v in _plain(record.final_state).items() if k not in ("node_results", "intent_text")}
    (out_dir / "state.json").write_text(json.dumps(state, indent=1, default=str))


def summarise(variant: str, run_id: str, ok: bool, duration_ms: float, results: list[NodeResult],
              trace_id: str | None, error: str | None) -> dict[str, Any]:
    by_engine: dict[str, dict[str, Any]] = {}
    nodes = []
    for r in results:
        e = by_engine.setdefault(r.engine, {"latency_ms": 0.0, "calls": 0, "input_tokens": 0,
                                            "output_tokens": 0, "costs": [], "retries": 0, "errors": 0})
        e["latency_ms"] += r.latency_ms
        e["calls"] += r.calls
        e["input_tokens"] += r.input_tokens
        e["output_tokens"] += r.output_tokens
        e["retries"] += r.retries
        e["errors"] += r.status != "ok"
        if r.engine in ("llm", "jev"):
            e["costs"].append(Cost(r.cost_usd, r.cost_source))
        nodes.append({"node": r.node, "attempt": r.attempt, "type": r.node_type, "engine": r.engine,
                      "model": r.model, "latency_ms": round(r.latency_ms, 1), "calls": r.calls,
                      "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                      "cost_usd": r.cost_usd, "cost_source": r.cost_source, "retries": r.retries,
                      "status": r.status, "span_id": r.span_id})
    engines = {}
    for name, e in by_engine.items():
        c = total(e["costs"]) if e["costs"] else Cost(0.0, "catalogue")
        engines[name] = {**{k: v for k, v in e.items() if k != "costs"}, "latency_ms": round(e["latency_ms"], 1),
                         "cost": c.as_dict()}
    all_costs = [c for e in by_engine.values() for c in e["costs"]]
    loops = {"risk_attempts": max([r.attempt for r in results if r.node == "assess_risks"] or [0]),
             "requirement_attempts": max([r.attempt for r in results if r.node == "classify_requirements"] or [0]),
             "prd_attempts": max([r.attempt for r in results if r.node == "validate_prd"] or [0])}
    return {
        "variant": variant, "run_id": run_id, "ok": ok, "error": error, "trace_id": trace_id,
        "total_latency_ms": round(duration_ms, 1),
        "inference_calls": sum(e["calls"] for n, e in by_engine.items() if n in ("llm", "jev")),
        "llm_calls": by_engine.get("llm", {}).get("calls", 0),
        "jev_calls": by_engine.get("jev", {}).get("calls", 0),
        "mcp_calls": by_engine.get("mcp", {}).get("calls", 0),
        "input_tokens": sum(e["input_tokens"] for e in by_engine.values()),
        "output_tokens": sum(e["output_tokens"] for e in by_engine.values()),
        "total_cost": total(all_costs).as_dict() if all_costs else Cost(0.0, "catalogue").as_dict(),
        "retries": sum(e["retries"] for e in by_engine.values()),
        "errors": sum(e["errors"] for e in by_engine.values()),
        "loops": loops, "nodes_executed": len(results), "engines": engines, "nodes": nodes,
    }
