"""Determinism experiment: replay every bounded node on one fixed input, K times per engine, and
measure how often identical inputs give identical decisions.

Decisions are the booleans and labels a node writes into the state (selected, applies, category,
moscow, ...), not the raw probabilities: a node is consistent when it would route the workflow the
same way, not when the third decimal agrees. Two metrics per node and engine:

- identical_rate: share of repeats whose complete decision set equals the modal decision set.
- decision_agreement: mean over individual decisions of the share of repeats agreeing with the
  mode for that decision. 1.0 means every decision came back the same every time.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .. import telemetry as T
from ..config import Settings
from ..engines import JevEngine, LLMDecisionEngine, OpenRouterLLM
from ..graph.nodes import BOUNDED, Nodes, RunContext, _plain
from ..knowledge import StaticKnowledge
from ..pricing import Pricing

SKIP_KEYS = {"evidence", "probabilities", "confidence"}


def decisions(output: Any, path: str = "") -> dict[str, Any]:
    """Leaf booleans, strings and ints of a node output, keyed by path. Floats are evidence, not decisions."""
    out: dict[str, Any] = {}
    if isinstance(output, dict):
        for k, v in output.items():
            if k in SKIP_KEYS or k.endswith("_probabilities") or k.startswith("p_"):
                continue
            out.update(decisions(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(output, list):
        for i, v in enumerate(output):
            key: Any = i
            if isinstance(v, dict):
                # the item's own id, or the id of the catalogue entry it wraps (persona, policy, ...)
                key = v.get("id") or next((x["id"] for x in v.values() if isinstance(x, dict) and "id" in x), i)
            out.update(decisions(v, f"{path}[{key}]"))
    elif isinstance(output, (bool, str, int)) and not isinstance(output, float):
        out[path] = output
    return out


def agreement(repeats: list[dict[str, Any]]) -> dict[str, Any]:
    if not repeats:
        return {"repeats": 0, "identical_rate": None, "decision_agreement": None, "decisions": 0}
    sets = [tuple(sorted(d.items())) for d in repeats]
    modal_count = Counter(sets).most_common(1)[0][1]
    keys = sorted({k for d in repeats for k in d})
    per_key = []
    unstable = []
    for k in keys:
        values = [d.get(k) for d in repeats]
        _, n = Counter(values).most_common(1)[0]
        per_key.append(n / len(repeats))
        if n < len(repeats):
            unstable.append({"decision": k, "values": dict(Counter(map(str, values)))})
    return {"repeats": len(repeats), "identical_rate": round(modal_count / len(repeats), 4),
            "decision_agreement": round(sum(per_key) / len(per_key), 4) if per_key else None,
            "decisions": len(keys), "unstable": unstable[:25]}


async def replay(settings: Settings, pricing: Pricing, telemetry: T.Telemetry, reference_state: dict,
                 engine: str, repeats: int, experiment_id: str,
                 nodes_to_test: list[str] | None = None) -> dict[str, Any]:
    """Run each bounded node `repeats` times on the reference state with the given engine."""
    llm = OpenRouterLLM(settings.llm, pricing, telemetry)
    decider = JevEngine(settings.jev, pricing, telemetry) if engine == "jev" \
        else LLMDecisionEngine(llm, settings.llm, settings.jev)
    ctx = RunContext(settings=settings, llm=llm, decider=decider, knowledge=StaticKnowledge(telemetry),
                     telemetry=telemetry, variant=f"consistency-{engine}", experiment_id=experiment_id,
                     run_id=f"consistency-{engine}")
    nodes = Nodes(ctx)
    results: dict[str, Any] = {}
    try:
        for name, spec in nodes.specs.items():
            if spec.node_type != BOUNDED or (nodes_to_test and name not in nodes_to_test):
                continue
            if any(k not in reference_state for k in spec.inputs):
                results[name] = {"skipped": f"reference state lacks {spec.inputs}"}
                continue
            run = nodes.wrap(spec)
            outputs, latencies, costs, errors = [], [], [], 0
            for _ in range(repeats):
                try:
                    update = await run(dict(reference_state))
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    results.setdefault(name, {}).setdefault("errors_detail", []).append(str(e)[:200])
                    continue
                rec = update["node_results"][0]
                outputs.append(decisions({k: _plain(v) for k, v in update.items()
                                          if k not in ("node_results", "errors", spec.attempt_key)}))
                latencies.append(rec.latency_ms)
                costs.append(rec.cost_usd)
            stats = agreement(outputs)
            results[name] = {**results.get(name, {}), **stats, "engine": engine, "model": decider.model,
                             "errors": errors,
                             "median_latency_ms": round(sorted(latencies)[len(latencies) // 2], 1) if latencies else None,
                             "mean_cost_usd": (None if not costs or any(c is None for c in costs)
                                               else round(sum(costs) / len(costs), 6))}
    finally:
        await llm.aclose()
        if hasattr(decider, "aclose"):
            await decider.aclose()
    return results
