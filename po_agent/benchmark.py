"""The benchmark: both variants, N times each, interleaved; blind quality judging per pair;
the consistency replay; then comparison.json and comparison.md.

Nothing in here invents a number. Every figure is a measurement from a run directory, and an
unknown cost stays unknown all the way to the report.
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

from . import telemetry as T
from .config import Settings
from .evaluation.consistency import replay
from .evaluation.judge import DIMENSIONS, judge_pair
from .pricing import Pricing
from .runner import VARIANTS, RunRecord, new_experiment_id, run_variant


NODE_ORDER = ["parse_intent", "retrieve_knowledge", "select_capabilities", "select_personas", "identify_outcomes",
              "retrieve_constraints", "assess_constraints", "discover_risks", "assess_risks", "investigate_risks",
              "generate_requirements", "classify_requirements", "check_coverage", "refine_requirements",
              "construct_prd", "validate_prd", "refine_prd", "finalise"]


def _stats(values: list[float]) -> dict[str, float | None]:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None, "variance": None}
    s = sorted(vals)
    p95 = s[min(len(s) - 1, max(0, round(0.95 * len(s)) - 1))]
    return {"n": len(s), "mean": round(statistics.fmean(s), 6), "median": round(statistics.median(s), 6),
            "p95": round(p95, 6), "min": round(s[0], 6), "max": round(s[-1], 6),
            "variance": round(statistics.pvariance(s), 6) if len(s) > 1 else 0.0}


def _cost_stats(costs: list[dict]) -> dict[str, Any]:
    if any(c["usd"] is None for c in costs):
        return {"known": False, "note": "at least one run has an unknown cost", **_stats([])}
    return {"known": True, "source": sorted({c["source"] for c in costs}), **_stats([c["usd"] for c in costs])}


def aggregate_variant(metrics: list[dict]) -> dict[str, Any]:
    # A run marked partial (a PARTIAL file in its directory, see `po-agent report`) contributes its
    # completed nodes to node-level statistics but is not a run at run level.
    complete = [m for m in metrics if not m.get("partial")]
    ok = [m for m in complete if m["ok"]]
    # Node-level statistics use every node execution that completed, including those inside runs
    # that failed later: a node's latency and cost do not depend on what happened after it.
    nodes: dict[str, dict[str, list]] = {}
    for m in metrics:
        for n in m["nodes"]:
            if n["status"] != "ok":
                continue
            d = nodes.setdefault(n["node"], {"latency_ms": [], "input_tokens": [], "output_tokens": [],
                                             "cost_usd": [], "calls": [], "retries": [], "engine": set(),
                                             "model": set(), "executions": 0})
            d["latency_ms"].append(n["latency_ms"])
            d["input_tokens"].append(n["input_tokens"])
            d["output_tokens"].append(n["output_tokens"])
            d["cost_usd"].append(n["cost_usd"])
            d["calls"].append(n["calls"])
            d["retries"].append(n["retries"])
            d["engine"].add(n["engine"])
            d["model"].add(n["model"] or "")
            d["executions"] += 1
    node_summary = {}
    for name, d in nodes.items():
        node_summary[name] = {
            "engine": sorted(d["engine"]), "model": sorted(x for x in d["model"] if x),
            "executions": d["executions"], "executions_per_run": round(d["executions"] / max(1, len(metrics)), 2),
            "latency_ms": _stats(d["latency_ms"]), "input_tokens": _stats(d["input_tokens"]),
            "output_tokens": _stats(d["output_tokens"]),
            "cost_usd": (_stats(d["cost_usd"]) if all(c is not None for c in d["cost_usd"])
                         else {"known": False, **_stats([])}),
            "calls": sum(d["calls"]), "retries": sum(d["retries"])}
    engines: dict[str, dict[str, Any]] = {}
    for eng in ("llm", "jev", "mcp"):
        rows = [m["engines"].get(eng) for m in ok if m["engines"].get(eng)]
        if rows:
            engines[eng] = {"latency_ms": _stats([r["latency_ms"] for r in rows]),
                            "calls": _stats([r["calls"] for r in rows]),
                            "input_tokens": _stats([r["input_tokens"] for r in rows]),
                            "output_tokens": _stats([r["output_tokens"] for r in rows]),
                            "cost_usd": _cost_stats([r["cost"] for r in rows])}
    return {
        "runs": len(complete), "succeeded": len(ok), "failure_rate": round(1 - len(ok) / max(1, len(complete)), 4),
        "partial_runs": len(metrics) - len(complete),
        "errors": [m["error"] for m in complete if not m["ok"]],
        "total_latency_ms": _stats([m["total_latency_ms"] for m in ok]),
        "llm_calls": _stats([m["llm_calls"] for m in ok]), "jev_calls": _stats([m["jev_calls"] for m in ok]),
        "mcp_calls": _stats([m["mcp_calls"] for m in ok]),
        "input_tokens": _stats([m["input_tokens"] for m in ok]),
        "output_tokens": _stats([m["output_tokens"] for m in ok]),
        "total_cost_usd": _cost_stats([m["total_cost"] for m in ok]),
        "retries": _stats([m["retries"] for m in ok]), "node_errors": _stats([m["errors"] for m in ok]),
        "loops": {k: _stats([m["loops"][k] for m in ok]) for k in ("risk_attempts", "requirement_attempts", "prd_attempts")},
        "engines": engines, "nodes": node_summary,
        "trace_ids": [m["trace_id"] for m in ok if m.get("trace_id")],
    }


def aggregate_quality(judgements: list[dict], variants: list[str]) -> dict[str, Any]:
    if not judgements:
        return {"judgements": 0}
    per_dim = {v: {d: _stats([j["scores"][v].get(d) for j in judgements if d in j["scores"].get(v, {})])
                   for d in DIMENSIONS} for v in variants}
    totals = {v: _stats([j["totals"][v] for j in judgements]) for v in variants}
    prefs = {v: sum(j["preferred"] == v for j in judgements) for v in variants}
    prefs["tie"] = sum(j["preferred"] == "tie" for j in judgements)
    return {"judgements": len(judgements), "judge_model": judgements[0]["judge_model"],
            "total_score": totals, "per_dimension": per_dim, "preferred_counts": prefs,
            "judge_cost_usd": (sum(j["usage"]["cost"]["usd"] for j in judgements)
                               if all(j["usage"]["cost"]["usd"] is not None for j in judgements) else None)}


async def run_benchmark(settings: Settings, intent_path: Path, runs: int, pricing: Pricing,
                        telemetry: T.Telemetry, consistency_repeats: int | None = None,
                        judge: bool = True, variants: tuple[str, ...] = VARIANTS,
                        out_root: Path | None = None, seed: int | None = None) -> dict[str, Any]:
    experiment_id = new_experiment_id()
    root = Path(out_root or settings.benchmark.results_dir) / experiment_id
    root.mkdir(parents=True, exist_ok=True)
    intent = intent_path.read_text()
    shutil.copy(intent_path, root / "intent.md")
    (root / "config.yaml").write_text(_yaml(settings.effective()))
    (root / "pricing.yaml").write_text(_yaml(pricing.snapshot()))
    rng = random.Random(seed)
    print(f"experiment {experiment_id} -> {root}")

    metrics: dict[str, list[dict]] = {v: [] for v in variants}
    records: dict[str, list[RunRecord]] = {v: [] for v in variants}
    judgements: list[dict] = []
    started = time.perf_counter()
    for n in range(1, runs + 1):
        order = list(variants) if n % 2 else list(reversed(variants))   # alternate who goes first
        for v in order:
            run_id = f"{v}-{n:02d}"
            rec = await run_variant(settings, v, intent, experiment_id, run_id, pricing, telemetry,
                                    out_dir=root / v / f"run-{n:02d}")
            records[v].append(rec)
            m = rec.metrics()
            metrics[v].append(m)
            cost = m["total_cost"]["usd"]
            print(f"  run {n:02d} {v:7s} {'ok ' if rec.ok else 'ERR'} {m['total_latency_ms'] / 1000:7.1f}s "
                  f"llm={m['llm_calls']:2d} jev={m['jev_calls']:2d} tokens={m['input_tokens'] + m['output_tokens']:6d} "
                  f"cost={f'${cost:.4f}' if cost is not None else 'unknown'}"
                  + (f"  {rec.error}" if rec.error else ""))
        telemetry.flush()
        if judge and len(variants) == 2 and all(records[v][-1].ok for v in variants):
            prds = {v: records[v][-1].prd for v in variants}
            orders = [tuple(variants), tuple(reversed(variants))] if settings.benchmark.judge_both_orders \
                else [tuple(rng.sample(list(variants), 2))]
            for order in orders:
                try:
                    j = await judge_pair(settings, pricing, telemetry, intent, prds, order=order, rng=rng)
                    j["run"] = n
                    judgements.append(j)
                    print(f"  judge {n:02d} A={order[0]} B={order[1]} -> prefers {j['preferred']} "
                          f"totals={j['totals']}")
                except Exception as e:  # noqa: BLE001
                    print(f"  judge {n:02d} failed: {e}")
                    judgements.append({"run": n, "error": str(e), "mapping": dict(zip("AB", order)),
                                       "scores": {}, "totals": {}, "preferred": "error", "judge_model": "",
                                       "usage": {"cost": {"usd": None}}})

    consistency: dict[str, Any] = {}
    repeats = settings.benchmark.consistency_repeats if consistency_repeats is None else consistency_repeats
    reference = next((r for v in variants for r in records[v] if r.ok), None)
    if repeats and reference:
        print(f"  consistency: replaying bounded nodes {repeats}x per engine on {reference.run_id}'s state")
        for engine in ("llm", "jev"):
            consistency[engine] = await replay(settings, pricing, telemetry, reference.final_state, engine,
                                               repeats, experiment_id)
            for node, r in consistency[engine].items():
                if "identical_rate" in r:
                    print(f"    {engine:4s} {node:22s} identical={r['identical_rate']:.2f} "
                          f"agreement={r['decision_agreement']:.3f} median={r['median_latency_ms']}ms")
        consistency["reference_run"] = reference.run_id
        consistency["repeats"] = repeats
    telemetry.flush()

    good = [j for j in judgements if "error" not in j]
    comparison = {
        "experiment_id": experiment_id, "intent": str(intent_path), "runs": runs, "variants": list(variants),
        "wall_time_s": round(time.perf_counter() - started, 1),
        "config": {"llm_model": settings.llm.model, "decision_model": settings.llm.decision_model or settings.llm.model,
                   "jev_model": settings.jev.model, "judge_model": settings.judge.model},
        "variants_summary": {v: aggregate_variant(metrics[v]) for v in variants},
        "quality": aggregate_quality(good, list(variants)),
        "consistency": consistency,
    }
    (root / "evaluation").mkdir(exist_ok=True)
    (root / "evaluation" / "quality.json").write_text(json.dumps(judgements, indent=1))
    (root / "evaluation" / "consistency.json").write_text(json.dumps(consistency, indent=1))
    (root / "comparison.json").write_text(json.dumps(comparison, indent=1))
    (root / "comparison.md").write_text(render_report(comparison))
    print(f"\n{render_report(comparison)}")
    return comparison


def _yaml(data: dict) -> str:
    import yaml
    return yaml.safe_dump(data, sort_keys=False)


def _fmt(x, unit: str = "", digits: int = 1) -> str:
    if x is None:
        return "n/a"
    if unit == "$":
        return f"${x:.4f}" if x >= 0.001 else f"${x:.5f}"
    if unit == "s":
        return f"{x / 1000:.{digits}f}s"
    if unit == "%":
        return f"{x * 100:.0f}%"
    return f"{x:.{digits}f}" if isinstance(x, float) else str(x)


def render_report(c: dict[str, Any]) -> str:
    vs = c["variants"]
    S = c["variants_summary"]
    w = 16
    lines = ["=" * 64, "PRODUCT OWNER BENCHMARK", "=" * 64,
             f"experiment {c['experiment_id']}  runs={c['runs']}  intent={c['intent']}",
             (f"llm={c['config']['llm_model']}  decision(llm)={c['config']['decision_model']}  "
              f"jev={c['config']['jev_model']}  judge={c['config']['judge_model']}"), "",
             f"{'':24}" + "".join(f"{v.upper():>{w}}" for v in vs), "-" * 64]

    def row(label, values):
        lines.append(f"{label:24}" + "".join(f"{x!s:>{w}}" for x in values))

    row("Runs ok / total", [f"{S[v]['succeeded']}/{S[v]['runs']}"
                            + (f" (+{S[v]['partial_runs']} partial)" if S[v].get("partial_runs") else "") for v in vs])
    row("Total latency (median)", [_fmt(S[v]["total_latency_ms"]["median"], "s") for v in vs])
    row("P95 latency", [_fmt(S[v]["total_latency_ms"]["p95"], "s") for v in vs])
    row("Min / max latency", [f"{_fmt(S[v]['total_latency_ms']['min'], 's')} / {_fmt(S[v]['total_latency_ms']['max'], 's')}" for v in vs])
    row("LLM calls (median)", [_fmt(S[v]["llm_calls"]["median"], "", 0) for v in vs])
    row("Jev calls (median)", [_fmt(S[v]["jev_calls"]["median"], "", 0) for v in vs])
    row("Input tokens (median)", [_fmt(S[v]["input_tokens"]["median"], "", 0) for v in vs])
    row("Output tokens (median)", [_fmt(S[v]["output_tokens"]["median"], "", 0) for v in vs])
    row("Total cost (median)", [_fmt(S[v]["total_cost_usd"].get("median"), "$") if S[v]["total_cost_usd"].get("known") else "unknown" for v in vs])
    row("Errors / retries (sum)", [(f"{int(S[v]['node_errors']['mean'] * S[v]['node_errors']['n']) if S[v]['node_errors']['n'] else 0} / "
                                    f"{int(S[v]['retries']['mean'] * S[v]['retries']['n']) if S[v]['retries']['n'] else 0}") for v in vs])
    row("Failure rate", [_fmt(S[v]["failure_rate"], "%") for v in vs])
    cons = c.get("consistency", {})
    eng_for = {"llm": "llm", "hybrid": "jev"}
    row("Consistency (bounded)", [_consistency_summary(cons.get(eng_for.get(v, ""), {})) for v in vs])
    q = c.get("quality", {})
    row("PRD quality (mean /50)", [_fmt(q.get("total_score", {}).get(v, {}).get("mean")) if q.get("judgements") else "n/a" for v in vs])
    row("Judge preferred", [str(q.get("preferred_counts", {}).get(v, "n/a")) for v in vs]
        + ([f"tie {q['preferred_counts'].get('tie', 0)}"] if q.get("judgements") else []))
    lines += ["=" * 64, "", ("## Node-level breakdown (median per execution, across all runs including "
                             "nodes that completed inside runs that failed later)"), ""]
    seen = {n for v in vs for n in S[v]["nodes"]}
    names = [n for n in NODE_ORDER if n in seen] + sorted(seen - set(NODE_ORDER))
    lines.append(f"{'node':24}{'':2}" + "".join(f"{v:>{w * 2}}" for v in vs))
    lines.append(f"{'':26}" + "".join(f"{'engine   latency   tokens   cost':>{w * 2}}" for _ in vs))
    for n in names:
        cells = []
        for v in vs:
            d = S[v]["nodes"].get(n)
            if not d:
                cells.append(f"{'-':>{w * 2}}")
                continue
            eng = "/".join(d["engine"])
            tok = (d["input_tokens"]["median"] or 0) + (d["output_tokens"]["median"] or 0)
            cost = _fmt(d["cost_usd"].get("median"), "$") if d["cost_usd"].get("median") is not None else "n/a"
            cells.append(f"{eng:>8}{_fmt(d['latency_ms']['median'], 's'):>10}{int(tok):>9}{cost:>10}".rjust(w * 2))
        lines.append(f"{n:24}{'':2}" + "".join(cells))
    if cons:
        lines += ["", f"## Consistency (bounded nodes replayed {cons.get('repeats')}x on {cons.get('reference_run')}'s state)", "",
                  f"{'node':24}{'llm identical':>15}{'llm agree':>11}{'jev identical':>15}{'jev agree':>11}"]
        for n in names:
            a, b = cons.get("llm", {}).get(n), cons.get("jev", {}).get(n)
            if not a and not b:
                continue
            lines.append(f"{n:24}{_c(a, 'identical_rate'):>15}{_c(a, 'decision_agreement'):>11}"
                         f"{_c(b, 'identical_rate'):>15}{_c(b, 'decision_agreement'):>11}")
    if q.get("judgements"):
        lines += ["", f"## Quality ({q['judgements']} blind judgements by {q['judge_model']}, 1-5 per dimension)", "",
                  f"{'dimension':28}" + "".join(f"{v:>{w}}" for v in vs)]
        for d in DIMENSIONS:
            lines.append(f"{d:28}" + "".join(f"{_fmt(q['per_dimension'][v][d]['mean'], '', 2):>{w}}" for v in vs))
    lines += ["", ("Costs are provider-reported where available (source shown in comparison.json); "
                   "'unknown' means at least one call had no reported or catalogued price."),
              ("Quality and consistency are separate measurements from latency/tokens/cost and were "
               "never used to select runs.")]
    return "\n".join(lines) + "\n"


def _consistency_summary(d: dict) -> str:
    rates = [r["decision_agreement"] for r in d.values() if isinstance(r, dict) and r.get("decision_agreement") is not None]
    return f"{statistics.fmean(rates):.3f} agree" if rates else "n/a"


def _c(d, key):
    return "n/a" if not d or d.get(key) is None else f"{d[key]:.3f}"
