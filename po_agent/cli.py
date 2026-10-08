"""Entry points.

    po-agent run --variant llm|hybrid --intent FILE      one run through the ADK agent
    po-agent benchmark --intent FILE --runs N            both variants, judge, consistency, report
    po-agent check                                       key, collector, Jev, LLM, MCP server
    po-agent graph                                       the graph as Mermaid, from the code
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import Settings, load_settings
from .pricing import Pricing
from .telemetry import Telemetry, install


def _boot(args) -> tuple[Settings, Pricing, Telemetry]:
    settings = load_settings(args.config, args.pricing)
    if args.no_telemetry:
        settings.telemetry.enabled = False
    pricing = Pricing.load(settings.pricing_path)
    telemetry = install(Telemetry(settings.telemetry))
    return settings, pricing, telemetry


async def cmd_run(args) -> int:
    from .adk_app import run_via_adk
    settings, pricing, telemetry = _boot(args)
    intent = Path(args.intent).read_text()
    out = Path(args.out) if args.out else None
    try:
        record, meta = await run_via_adk(settings, pricing, telemetry, args.variant, intent, out)
    finally:
        telemetry.shutdown()
    m = record.metrics()
    cost = m["total_cost"]["usd"]
    print(f"\n{args.variant}: {'ok' if record.ok else 'FAILED'} in {m['total_latency_ms'] / 1000:.1f}s; "
          f"llm calls {m['llm_calls']}, jev calls {m['jev_calls']}, tokens {m['input_tokens']}+{m['output_tokens']}, "
          f"cost {f'${cost:.4f}' if cost is not None else 'unknown'}; "
          f"loops {m['loops']}; out {meta.get('out_dir')}; trace {record.trace_id}")
    if record.error:
        print(record.error, file=sys.stderr)
    return 0 if record.ok else 1


async def cmd_benchmark(args) -> int:
    from .benchmark import run_benchmark
    settings, pricing, telemetry = _boot(args)
    variants = tuple(args.variants.split(","))
    try:
        await run_benchmark(settings, Path(args.intent), args.runs, pricing, telemetry,
                            consistency_repeats=args.consistency, judge=not args.no_judge, variants=variants,
                            out_root=Path(args.out) if args.out else None, seed=args.seed)
    finally:
        telemetry.shutdown()
    return 0


async def cmd_check(args) -> int:
    import httpx

    from .engines import JevEngine, OpenRouterLLM
    from .knowledge import MCPKnowledge
    settings, pricing, telemetry = _boot(args)
    ok = True

    def report(label, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  [{'ok' if good else 'FAIL'}] {label} {detail}")

    print("config:", settings.config_path)
    report("LLM api key", settings.llm.api_key, f"({settings.llm.api_key_env})")
    report("Jev api key", settings.jev.api_key or not settings.jev.api_key_env, f"({settings.jev.api_key_env or 'none needed'})")
    async with httpx.AsyncClient(timeout=5) as c:
        try:
            health = settings.telemetry.otlp_endpoint.replace(":4318", ":13133").replace(":4317", ":13133")
            r = await c.get(health)
            report("OTel collector", r.status_code == 200, health)
        except Exception as e:  # noqa: BLE001
            report("OTel collector", False, str(e))
    try:
        async with MCPKnowledge(settings.knowledge, telemetry) as k:
            tools = await k.session.list_tools()
            report("MCP knowledge server", len(tools.tools) >= 7, f"{len(tools.tools)} tools")
    except Exception as e:  # noqa: BLE001
        report("MCP knowledge server", False, str(e)[:200])
    jev = JevEngine(settings.jev, pricing, telemetry)
    try:
        r = await jev.decide(state="A customer asks to change the delivery day.", purpose="check",
                             questions={"q": {"type": "noul", "instructions": "The customer wants to reschedule."}})
        report("Jev", True, f"{settings.jev.model} P(yes)={r.answers['q'].noul:.2f} in {r.latency_ms:.0f}ms "
                            f"cost={r.usage.cost.as_dict()}")
    except Exception as e:  # noqa: BLE001
        report("Jev", False, str(e)[:200])
    finally:
        await jev.aclose()
    llm = OpenRouterLLM(settings.llm, pricing, telemetry)
    try:
        r = await llm.generate(system="Answer in one word.", prompt="Say OK.", schema=None, purpose="check", max_tokens=5)
        report("LLM", True, f"{r.model} in {r.latency_ms:.0f}ms cost={r.usage.cost.as_dict()}")
    except Exception as e:  # noqa: BLE001
        report("LLM", False, str(e)[:200])
    finally:
        await llm.aclose()
    telemetry.shutdown()
    return 0 if ok else 1


async def cmd_judge(args) -> int:
    """Blind-judge every run pair in an experiment directory and (re)write evaluation/quality.json."""
    import json

    from .evaluation.judge import judge_pair
    settings, pricing, telemetry = _boot(args)
    if args.model:
        settings.judge.model = args.model
    if args.max_tokens:
        settings.judge.max_tokens = args.max_tokens
    root = Path(args.experiment)
    intent = (root / "intent.md").read_text()
    runs = sorted(p.name for p in (root / "llm").glob("run-*") if (root / "hybrid" / p.name / "prd.md").exists())
    judgements = []
    try:
        for run in runs:
            prds = {v: (root / v / run / "prd.md").read_text() for v in ("llm", "hybrid")}
            if not all(prds.values()):
                print(f"  {run}: skipped (a PRD is empty)")
                continue
            for order in [("llm", "hybrid"), ("hybrid", "llm")] if settings.benchmark.judge_both_orders else [("llm", "hybrid")]:
                j = await judge_pair(settings, pricing, telemetry, intent, prds, order=order)
                j["run"] = int(run.split("-")[1])
                judgements.append(j)
                print(f"  {run} A={order[0]} B={order[1]} -> prefers {j['preferred']} totals={j['totals']}")
    finally:
        telemetry.shutdown()
    (root / "evaluation").mkdir(exist_ok=True)
    (root / "evaluation" / "quality.json").write_text(json.dumps(judgements, indent=1))
    print(f"wrote {root / 'evaluation' / 'quality.json'} ({len(judgements)} judgements by {settings.judge.model})")
    return 0


def cmd_report(args) -> int:
    """Rebuild comparison.json/.md for an experiment directory from its run files."""
    import json

    from .benchmark import aggregate_quality, aggregate_variant, render_report
    root = Path(args.experiment)
    variants = [v for v in ("llm", "hybrid") if (root / v).is_dir()]
    metrics = {v: [{**json.loads(p.read_text()), "partial": (p.parent / "PARTIAL").exists()}
                   for p in sorted((root / v).glob("run-*/metrics.json"))] for v in variants}
    quality_file = root / "evaluation" / "quality.json"
    judgements = [j for j in json.loads(quality_file.read_text()) if "error" not in j] if quality_file.exists() else []
    cons_file = root / "evaluation" / "consistency.json"
    config = json.loads(json.dumps(__import__("yaml").safe_load((root / "config.yaml").read_text())))
    comparison = {
        "experiment_id": root.name, "intent": str(root / "intent.md"), "runs": max(len(m) for m in metrics.values()),
        "variants": variants, "wall_time_s": None,
        "config": {"llm_model": config["llm"]["model"], "decision_model": config["llm"].get("decision_model") or config["llm"]["model"],
                   "jev_model": config["jev"]["model"], "judge_model": config["judge"]["model"]},
        "variants_summary": {v: aggregate_variant(metrics[v]) for v in variants},
        "quality": aggregate_quality(judgements, variants),
        "consistency": json.loads(cons_file.read_text()) if cons_file.exists() else {},
    }
    (root / "comparison.json").write_text(json.dumps(comparison, indent=1))
    (root / "comparison.md").write_text(render_report(comparison))
    print(render_report(comparison))
    return 0


def cmd_graph(args) -> int:
    from .graph.build import mermaid
    print(mermaid())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="po-agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None)
    p.add_argument("--pricing", default=None)
    p.add_argument("--no-telemetry", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--variant", choices=["llm", "hybrid"], required=True)
    r.add_argument("--intent", required=True)
    r.add_argument("--out", default=None)
    b = sub.add_parser("benchmark")
    b.add_argument("--intent", required=True)
    b.add_argument("--runs", type=int, default=None)
    b.add_argument("--consistency", type=int, default=None, help="repeats per bounded node per engine (0 to skip)")
    b.add_argument("--no-judge", action="store_true")
    b.add_argument("--variants", default="llm,hybrid")
    b.add_argument("--out", default=None)
    b.add_argument("--seed", type=int, default=None)
    sub.add_parser("check")
    sub.add_parser("graph")
    rp = sub.add_parser("report", help="rebuild comparison.md from an experiment directory")
    rp.add_argument("experiment")
    jp = sub.add_parser("judge", help="blind-judge the PRD pairs in an experiment directory")
    jp.add_argument("experiment")
    jp.add_argument("--model", default=None, help="override judge.model")
    jp.add_argument("--max-tokens", type=int, default=None, help="override judge.max_tokens")
    args = p.parse_args(argv)
    if args.cmd == "graph":
        return cmd_graph(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "judge":
        return asyncio.run(cmd_judge(args))
    if args.cmd == "benchmark" and args.runs is None:
        args.runs = load_settings(args.config, args.pricing).benchmark.runs
    return asyncio.run({"run": cmd_run, "benchmark": cmd_benchmark, "check": cmd_check}[args.cmd](args))


if __name__ == "__main__":
    sys.exit(main())
