"""Generate grafana/po-benchmark.json (and optionally push it to Grafana).

    python tools/gendashboard.py                       # write the JSON
    python tools/gendashboard.py --push http://127.0.0.1:3001

The board is generated so that the node layout follows the graph and the queries use the
metric names Prometheus actually stores for the OTLP instruments (po.node.duration ->
po_node_duration_milliseconds_bucket, po.node.cost -> po_node_cost_USD_total, ...).
Datasource UIDs match the local otel-stack: prometheus, tempo.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grafana" / "po-benchmark.json"
PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}
SERVICE = "product-owner-agent-demo"

# The workflow, in reading order, with the node type (colour) and the row it sits on.
WORKFLOW = [
    ("parse_intent", "generative"), ("retrieve_knowledge", "retrieval"), ("select_capabilities", "bounded"),
    ("select_personas", "bounded"), ("identify_outcomes", "generative"), ("retrieve_constraints", "retrieval"),
    ("assess_constraints", "bounded"), ("discover_risks", "generative"), ("assess_risks", "bounded"),
    ("investigate_risks", "generative"), ("generate_requirements", "generative"),
    ("classify_requirements", "bounded"), ("check_coverage", "bounded"), ("refine_requirements", "generative"),
    ("construct_prd", "generative"), ("validate_prd", "bounded"), ("refine_prd", "generative"),
    ("finalise", "deterministic"),
]
COLOUR = {"generative": "blue", "bounded": "red", "retrieval": "green", "deterministic": "text"}
VARIANT_FILTER = 'workflow_variant=~"$variant"'

_id = 0


def nid() -> int:
    global _id
    _id += 1
    return _id


def target(expr: str, legend: str, instant: bool = True, ref: str = "A") -> dict:
    return {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref,
            "instant": instant, "range": not instant, "format": "time_series"}


def stat(title: str, targets: list[dict], x: int, y: int, w: int, h: int, unit: str = "none",
         decimals: int | None = None, colour: str = "text", description: str = "", links: list | None = None) -> dict:
    return {
        "id": nid(), "type": "stat", "title": title, "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h}, "datasource": PROM, "targets": targets,
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                    "orientation": "horizontal", "textMode": "value_and_name", "colorMode": "background",
                    "graphMode": "none", "justifyMode": "center", "wideLayout": True},
        "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "color": {"mode": "fixed", "fixedColor": colour},
                                     "noValue": "–", "links": links or []}, "overrides": []},
    }


# Counters are cumulative per process (service_instance_id), so each series' max over the range is
# that process's final value; summing them across series gives the total across runs. This works
# with a single sample per series, which rate()/increase() do not.
NO_VARIANT = ("po_inference", "po_mcp")   # engine- and MCP-level metrics have no workflow_variant label


def _sel(metric: str, extra: str) -> str:
    parts = [] if metric.startswith(NO_VARIANT) else [VARIANT_FILTER]
    if extra:
        parts.append(extra)
    return "{" + ", ".join(parts) + "}" if parts else ""


def q50(metric: str, by: str, extra: str = "") -> str:
    return (f"histogram_quantile(0.5, sum by (le, {by}) "
            f"(max_over_time({metric}_bucket{_sel(metric, extra)}[$__range])))")


def inc(metric: str, by: str, extra: str = "") -> str:
    return f"sum by ({by}) (max_over_time({metric}{_sel(metric, extra)}[$__range]))"


def explore_link(node: str) -> dict:
    q = {"datasource": "tempo", "queries": [{"refId": "A", "queryType": "traceql",
         "query": f'{{resource.service.name="{SERVICE}" && name="{node}"}}', "limit": 20}],
         "range": {"from": "${__from}", "to": "${__to}"}}
    return {"title": f"Traces for {node} (Tempo)", "targetBlank": True,
            "url": "/explore?schemaVersion=1&panes=" + json.dumps({"po": q}, separators=(",", ":"))}


def build() -> dict:
    panels = []
    y = 0
    # --- summary row ------------------------------------------------------------------------
    panels.append({"id": nid(), "type": "row", "title": "Summary: baseline (llm) vs hybrid (llm + jev)",
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "collapsed": False, "panels": []})
    y += 1
    summary = [
        ("Total run latency (median)", [target(q50("po_run_duration_milliseconds", "workflow_variant"), "{{workflow_variant}}")], "ms", 0),
        ("LLM latency per run (median)", [target(q50("po_node_duration_milliseconds", "workflow_variant", 'engine_type="llm"'), "{{workflow_variant}}")], "ms", 0),
        ("Jev latency per node (median)", [target(q50("po_node_duration_milliseconds", "workflow_variant", 'engine_type="jev"'), "{{workflow_variant}}")], "ms", 0),
        ("MCP latency per call (median)", [target(q50("po_mcp_duration_milliseconds", "mcp_tool"), "{{mcp_tool}}")], "ms", 0),
        ("Input tokens", [target(inc("po_node_tokens_total", "workflow_variant", 'direction="input"'), "{{workflow_variant}}")], "short", 0),
        ("Output tokens", [target(inc("po_node_tokens_total", "workflow_variant", 'direction="output"'), "{{workflow_variant}}")], "short", 0),
        ("Total cost (USD)", [target(inc("po_node_cost_USD_total", "workflow_variant"), "{{workflow_variant}}")], "currencyUSD", 4),
        ("Inference calls", [target(inc("po_inference_calls_total", "engine_type", 'status="ok"'), "{{engine_type}}")], "short", 0),
        ("Retries", [target(inc("po_inference_retries_total", "engine_type"), "{{engine_type}}")], "short", 0),
        ("Errors (failed calls)", [target(inc("po_inference_calls_total", "engine_type", 'status="error"'), "{{engine_type}}")], "short", 0),
        ("Runs", [target(inc("po_run_outcome_total", "workflow_variant, status"), "{{workflow_variant}} {{status}}")], "short", 0),
    ]
    for i, (title, targets, unit, dec) in enumerate(summary):
        panels.append(stat(title, targets, (i % 4) * 6, y + (i // 4) * 4, 6, 4, unit, dec, "text",
                           "Over the dashboard time range; set the range to one experiment."))
    y += 12

    # --- workflow row ------------------------------------------------------------------------
    panels.append({"id": nid(), "type": "row", "title": "Workflow: median latency per node, llm vs hybrid (click a panel title for traces)",
                   "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "collapsed": False, "panels": []})
    y += 1
    per_row = 6
    for i, (node, ntype) in enumerate(WORKFLOW):
        x = (i % per_row) * 4
        yy = y + (i // per_row) * 5
        targets = [target(q50("po_node_duration_milliseconds", "workflow_variant, engine_type", f'graph_node_name="{node}"'),
                          "{{workflow_variant}} · {{engine_type}}")]
        panels.append(stat(f"{i + 1}. {node}", targets, x, yy, 4, 5, "ms", 0, COLOUR[ntype],
                           f"{ntype} node. Median latency per execution.", [explore_link(node)]))
    y += ((len(WORKFLOW) + per_row - 1) // per_row) * 5

    # --- node detail table ---------------------------------------------------------------------
    panels.append({"id": nid(), "type": "row", "title": "Node detail", "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                   "collapsed": False, "panels": []})
    y += 1
    by = "graph_node_name, workflow_variant, engine_type, model_name"
    table_targets = [
        {**target(q50("po_node_duration_milliseconds", by), "", ref="A"), "format": "table"},
        {**target(inc("po_node_tokens_total", by, 'direction="input"'), "", ref="B"), "format": "table"},
        {**target(inc("po_node_tokens_total", by, 'direction="output"'), "", ref="C"), "format": "table"},
        {**target(inc("po_node_cost_USD_total", by), "", ref="D"), "format": "table"},
        {**target(inc("po_node_duration_milliseconds_count", by), "", ref="E"), "format": "table"},
    ]
    panels.append({
        "id": nid(), "type": "table", "title": "Per node: median latency, tokens, cost, executions (time range)",
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 14}, "datasource": PROM, "targets": table_targets,
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Time 3": True,
                                                              "Time 4": True, "Time 5": True},
                                           "renameByName": {"Value #A": "median ms", "Value #B": "input tokens",
                                                            "Value #C": "output tokens", "Value #D": "cost USD",
                                                            "Value #E": "executions", "graph_node_name": "node",
                                                            "workflow_variant": "variant", "engine_type": "engine",
                                                            "model_name": "model"}}},
            {"id": "sortBy", "options": {"sort": [{"field": "node"}]}},
        ],
        "fieldConfig": {"defaults": {"decimals": 0}, "overrides": [
            {"matcher": {"id": "byName", "options": "cost USD"}, "properties": [{"id": "unit", "value": "currencyUSD"}, {"id": "decimals", "value": 5}]},
            {"matcher": {"id": "byName", "options": "median ms"}, "properties": [{"id": "unit", "value": "ms"}]}]},
        "options": {"showHeader": True, "cellHeight": "sm"},
    })
    y += 14

    # --- traces ------------------------------------------------------------------------------
    panels.append({"id": nid(), "type": "row", "title": "Traces", "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
                   "collapsed": False, "panels": []})
    y += 1
    for i, variant in enumerate(("llm", "hybrid")):
        panels.append({
            "id": nid(), "type": "table", "title": f"ProductOwner.Run traces: {variant}",
            "gridPos": {"x": i * 12, "y": y, "w": 12, "h": 10}, "datasource": TEMPO,
            "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "limit": 20, "tableType": "traces",
                         "query": f'{{resource.service.name="{SERVICE}" && name="ProductOwner.Run" && span.workflow.variant="{variant}"}}'}],
            "options": {"showHeader": True},
        })
    y += 10

    return {
        "uid": "po-benchmark", "title": "Product Owner benchmark: LLM vs LLM + Jev", "tags": ["benchmark", "jev", "langgraph"],
        "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "templating": {"list": [{
            "name": "variant", "label": "variant", "type": "query", "datasource": PROM, "includeAll": True, "multi": True,
            "query": {"query": "label_values(po_node_duration_milliseconds_count, workflow_variant)", "refId": "v"},
            "current": {"text": "All", "value": "$__all"}, "allValue": ".*", "refresh": 2}]},
        "panels": panels,
        "links": [{"title": "Explore traces", "type": "link", "url": "/explore?left=" + json.dumps(
            {"datasource": "tempo", "queries": [{"refId": "A", "queryType": "traceql",
             "query": f'{{resource.service.name="{SERVICE}"}}'}]}, separators=(",", ":"))}],
    }


def push(dashboard: dict, grafana: str) -> None:
    body = json.dumps({"dashboard": {**dashboard, "id": None}, "overwrite": True, "folderId": 0,
                       "message": "generated by tools/gendashboard.py"}).encode()
    req = urllib.request.Request(f"{grafana.rstrip('/')}/api/dashboards/db", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()})
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.load(r)
    print(f"pushed: {grafana.rstrip('/')}{out.get('url')}  (status {out.get('status')}, version {out.get('version')})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", metavar="GRAFANA_URL", default=None)
    args = ap.parse_args(argv)
    dashboard = build()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(dashboard, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT} ({len(dashboard['panels'])} panels)")
    if args.push:
        push(dashboard, args.push)
    return 0


if __name__ == "__main__":
    sys.exit(main())
