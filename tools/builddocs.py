"""Build docs/*.md into a static site: one design system, charts generated from the experiment's
own data (docs/data/comparison.json), the graph drawn from the compiled graph's own data
(docs/data/graph.json, written by `make graph`), Mermaid rendered client-side elsewhere.

    python tools/builddocs.py [--out site]

Markdown pages may use placeholders, each on its own line:
    {{hero}}                 the landing hero (workflow race strip + headline deltas)
    {{chart:race}}           the two-lane workflow strip
    {{chart:latency}}        bounded nodes, median latency, LLM vs Jev
    {{chart:cost}}           bounded nodes, median cost, LLM vs Jev
    {{chart:composition}}    where a run's time and cost go, per variant
    {{chart:consistency}}    decision agreement per bounded node, per engine
    {{chart:quality}}        blind judge scores per rubric dimension
    {{graph}}                the explorable graph diagram (docs/graph.md only: its node table is the text)
    {{example:key}}          a recorded payload from docs/data/examples.json (tools/genexamples.py) as a code
                             block; {{example:a|b}} puts two side by side; {{census}} is the question census table
Every chart ships with a table twin. Needs the `markdown` package only.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = DOCS / "data" / "comparison.json"
EXAMPLES = DOCS / "data" / "examples.json"
ORDER = ["index", "executive-summary", "architecture", "graph", "engine-selection", "examples", "configuration",
         "benchmark", "benchmark-results"]
NAV_TITLES = {"index": "Overview", "executive-summary": "Executive summary", "architecture": "Architecture",
              "graph": "The graph", "engine-selection": "Engine selection", "examples": "On the wire",
              "configuration": "Configuration", "benchmark": "Method", "benchmark-results": "Raw report"}

NODE_ORDER = ["parse_intent", "retrieve_knowledge", "select_capabilities", "select_personas", "identify_outcomes",
              "retrieve_constraints", "assess_constraints", "discover_risks", "assess_risks", "investigate_risks",
              "generate_requirements", "classify_requirements", "check_coverage", "refine_requirements",
              "construct_prd", "validate_prd", "refine_prd", "finalise"]
BOUNDED = ["select_capabilities", "select_personas", "assess_constraints", "assess_risks",
           "classify_requirements", "check_coverage", "validate_prd"]
NODE_LABEL = {n: n.replace("_", " ") for n in NODE_ORDER}
NODE_LABEL.update({"construct_prd": "write PRD", "validate_prd": "validate PRD", "refine_prd": "refine PRD",
                   "parse_intent": "parse intent", "retrieve_knowledge": "fetch catalogue",
                   "retrieve_constraints": "fetch policies"})
DIM_LABEL = {"intent_fidelity": "Intent fidelity", "requirement_coverage": "Requirement coverage",
             "persona_coverage": "Persona coverage", "business_outcome_coverage": "Business outcomes",
             "risk_coverage": "Risk coverage", "constraint_compliance": "Constraint compliance",
             "internal_consistency": "Internal consistency", "unsupported_assumptions": "No unsupported assumptions",
             "actionability": "Actionability", "prd_completeness": "Completeness"}

# --- page template -----------------------------------------------------------------------------

CSS = """
:root{color-scheme:light;
  --page:#f6f7f5;--surface:#ffffff;--ink:#15213a;--ink-2:#5b6475;--muted:#8a909c;--hair:#dcdfe4;--hair-2:#eceef1;
  --llm:#2a78d6;--jev:#eb6834;--mcp:#8a909c;--none:#c3c7cf;--wash:#eef3fb;--wash-jev:#fdf0ea;
  --ok:#0a7f3f;--font:"Manrope",system-ui,-apple-system,"Segoe UI",sans-serif;
  --g-gen-soft:color-mix(in srgb,var(--llm) 14%,var(--surface));--g-bounded-soft:color-mix(in srgb,var(--jev) 16%,var(--surface));
  --g-mcp-soft:color-mix(in srgb,var(--mcp) 16%,var(--surface));--g-det-soft:var(--surface);}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
  --page:#0f1218;--surface:#171b23;--ink:#f2f3f5;--ink-2:#b9bec8;--muted:#8a909c;--hair:#2a2f3a;--hair-2:#20242d;
  --llm:#3987e5;--jev:#d95926;--mcp:#8a909c;--none:#3a4050;--wash:#1b2638;--wash-jev:#33211a;--ok:#4cc38a;}}
:root[data-theme="dark"]{color-scheme:dark;
  --page:#0f1218;--surface:#171b23;--ink:#f2f3f5;--ink-2:#b9bec8;--muted:#8a909c;--hair:#2a2f3a;--hair-2:#20242d;
  --llm:#3987e5;--jev:#d95926;--mcp:#8a909c;--none:#3a4050;--wash:#1b2638;--wash-jev:#33211a;--ok:#4cc38a;}
*{box-sizing:border-box}
html{font-size:17px;-webkit-text-size-adjust:100%}
body{margin:0;font-family:var(--font);color:var(--ink);background:var(--page);line-height:1.6;font-weight:450}
a{color:inherit;text-decoration:underline;text-decoration-color:var(--hair);text-underline-offset:3px}
a:hover{text-decoration-color:var(--ink)}
:focus-visible{outline:2px solid var(--llm);outline-offset:3px;border-radius:2px}
.nav{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--page) 88%,transparent);backdrop-filter:blur(10px);
  border-bottom:1px solid var(--hair);}
.nav-in{max-width:1120px;margin:0 auto;padding:.7rem 1rem;display:flex;align-items:center;gap:1.25rem;flex-wrap:wrap}
.brand{font-weight:800;letter-spacing:-.01em;text-decoration:none;margin-right:auto;white-space:nowrap}
.brand span{color:var(--jev)}
.nav a.item{font-size:.92rem;color:var(--ink-2);text-decoration:none;padding:.2rem 0;border-bottom:2px solid transparent}
.nav a.item:hover,.nav a.item.current{color:var(--ink);border-bottom-color:var(--jev)}
main{max-width:1120px;margin:0 auto;padding:2rem 1rem 5rem}
.prose{max-width:72ch}
h1{font-size:clamp(2rem,4.5vw,3.1rem);line-height:1.08;letter-spacing:-.025em;font-weight:800;margin:.6rem 0 1rem;max-width:22ch}
h2{font-size:1.55rem;line-height:1.2;letter-spacing:-.015em;font-weight:800;margin:3rem 0 .8rem;padding-top:1.2rem;border-top:1px solid var(--hair)}
h3{font-size:1.1rem;font-weight:700;margin:1.8rem 0 .5rem}
p,li{color:var(--ink);max-width:72ch}
em{color:var(--ink-2)}
blockquote{margin:1.5rem 0;padding:.2rem 0 .2rem 1.1rem;border-left:3px solid var(--jev);color:var(--ink-2)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em;background:var(--hair-2);padding:.1em .35em;border-radius:4px}
pre{background:var(--surface);border:1px solid var(--hair);padding:1rem;overflow-x:auto;border-radius:8px;font-size:.84rem;line-height:1.5}
pre code{background:none;padding:0;font-size:inherit}
table{border-collapse:collapse;font-size:.92rem;display:block;overflow-x:auto;max-width:100%;margin:1rem 0 1.5rem}
th,td{border-bottom:1px solid var(--hair);padding:.5rem .7rem;vertical-align:top;text-align:left}
th{color:var(--ink-2);font-weight:600;border-bottom-color:var(--ink)}
td{font-variant-numeric:tabular-nums}
tr:hover td{background:var(--hair-2)}
img{max-width:100%;height:auto;border:1px solid var(--hair);border-radius:8px}
.mermaid{margin:1.2rem 0 2rem;overflow-x:auto}
hr{border:0;border-top:1px solid var(--hair);margin:2.5rem 0}
/* recorded payloads */
figure.code{margin:1.2rem 0 1.8rem;max-width:none}
figure.code figcaption{font-size:.86rem;font-weight:700;margin:0 0 .4rem;color:var(--ink-2);overflow-wrap:anywhere}
figure.code pre{margin:0;max-height:34rem;overflow:auto;font-size:.8rem}
figure.code .note{color:var(--muted);font-size:.84rem;margin:.4rem 0 0;max-width:none}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:1rem;width:calc(min(1120px,100vw) - 2rem);max-width:none;margin:1.2rem 0 1.8rem}
.pair figure.code{margin:0;min-width:0}
.pair figure.code pre{max-height:44rem}
@media (max-width:900px){.pair{grid-template-columns:1fr;width:auto}}
/* hero */
.hero{padding:2.5rem 0 1rem}
.hero .lede{font-size:1.2rem;color:var(--ink-2);max-width:62ch;margin:0 0 2rem;line-height:1.5}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:0 2rem;margin:1.5rem 0 .5rem;max-width:860px}
.kpi{padding:1rem 0;border-top:1px solid var(--hair)}
.kpi b{display:block;font-size:2.3rem;font-weight:800;letter-spacing:-.03em;line-height:1.1}
.kpi b.up{color:var(--ok)}
.kpi span{color:var(--ink-2);font-size:.95rem;line-height:1.35;display:block}
.kpi small{color:var(--muted)}
.hero-note{color:var(--muted);font-size:.88rem;max-width:72ch}
/* charts */
figure.chart{margin:1.5rem 0 2.2rem;padding:1.1rem 1.1rem .6rem;background:var(--surface);border:1px solid var(--hair);border-radius:10px}
figure.chart figcaption{font-weight:700;margin:0 0 .15rem}
figure.chart .sub{color:var(--ink-2);font-size:.9rem;margin:0 0 .8rem;max-width:80ch}
figure.chart svg{display:block;width:100%;height:auto;font-family:var(--font)}
.legend{display:flex;gap:1.2rem;flex-wrap:wrap;font-size:.86rem;color:var(--ink-2);margin:.5rem 0 .2rem}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;vertical-align:-1px;margin-right:.4rem}
details.twin{font-size:.88rem;margin:.4rem 0 0}
details.twin summary{cursor:pointer;color:var(--ink-2)}
details.twin table{font-size:.86rem;margin:.6rem 0 .4rem}
.svg-ink{fill:var(--ink)} .svg-ink2{fill:var(--ink-2)} .svg-muted{fill:var(--muted)}
.svg-hair{stroke:var(--hair)} .svg-llm{fill:var(--llm)} .svg-jev{fill:var(--jev)} .svg-mcp{fill:var(--mcp)} .svg-none{fill:var(--none)}
.svg-lane{stroke:var(--hair)} .svg-surface{stroke:var(--surface)}
.svg-llm-s{stroke:var(--llm)} .svg-jev-s{stroke:var(--jev)}
.contents{list-style:none;padding:0;margin:1.5rem 0;max-width:72ch}
.contents li{padding:.75rem 0;border-top:1px solid var(--hair)}
.contents li:last-child{border-bottom:1px solid var(--hair)}
.contents a{font-weight:700;text-decoration:none}
.contents a:hover{text-decoration:underline}
.contents span{display:block;color:var(--ink-2);font-size:.95rem}
footer{max-width:1120px;margin:0 auto;padding:1.5rem 1rem 3rem;color:var(--muted);font-size:.85rem;border-top:1px solid var(--hair)}
@media (max-width:640px){html{font-size:16px} .nav-in{gap:.8rem} h2{margin-top:2.2rem}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;450;500;600;700;800&display=swap" rel="stylesheet">
<style>{css}</style></head><body>
<nav class="nav"><div class="nav-in"><a class="brand" href="index.html">PO agent <span>×</span> Jev</a>{nav}</div></nav>
<main>{body}</main>
<footer>{footer}</footer>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({{startOnLoad:true, theme: dark ? "dark" : "neutral", themeVariables: {{fontFamily: "Manrope, system-ui, sans-serif"}}}});
</script>
</body></html>
"""


# --- helpers --------------------------------------------------------------------------------------


def esc(s) -> str:
    return html.escape(str(s))


def fmt_s(ms) -> str:
    if ms is None:
        return "–"
    return f"{ms / 1000:.1f} s" if ms >= 950 else f"{ms:.0f} ms"


def fmt_usd(x) -> str:
    if x is None:
        return "–"
    return f"${x:.4f}" if x >= 0.001 else f"${x:.5f}"


def pct(a, b) -> str:
    return f"{(b - a) / a * 100:+.0f} %".replace("-", "−") if a else "–"


def svg_text(x, y, s, cls="svg-ink", anchor="start", size=12, weight=500, extra="") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" font-size="{size}" '
            f'font-weight="{weight}" {extra}>{esc(s)}</text>')


def bar(x, y, w, h, cls, title) -> str:
    """A horizontal bar: square at the baseline (left), 4px rounded data-end (right)."""
    w = max(w, 0.0)
    r = min(4, w / 2, h / 2)
    if w <= 0.5:
        return ""
    d = (f"M{x:.1f},{y:.1f} H{x + w - r:.1f} a{r},{r} 0 0 1 {r},{r} V{y + h - r:.1f} "
         f"a{r},{r} 0 0 1 -{r},{r} H{x:.1f} Z")
    return f'<path d="{d}" class="{cls}"><title>{esc(title)}</title></path>'


def figure(caption, sub, svg, legend, twin_table) -> str:
    return (f'<figure class="chart"><figcaption>{esc(caption)}</figcaption><p class="sub">{esc(sub)}</p>'
            f'{legend}{svg}<details class="twin"><summary>Table view</summary>{twin_table}</details></figure>')


def legend(items) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:var(--{var})"></i>{esc(label)}</span>' for var, label in items) + "</div>"


def table(headers, rows) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


# --- charts -------------------------------------------------------------------------------------


class Charts:
    def __init__(self, comparison: dict):
        self.c = comparison
        self.S = comparison["variants_summary"]

    def node(self, variant, name):
        return self.S[variant]["nodes"].get(name)

    def engine_of(self, variant, name):
        n = self.node(variant, name)
        if not n:
            return None
        e = n["engine"][0] if n["engine"] else "none"
        return e

    # -- the race strip ---------------------------------------------------------------------------
    def race(self, compact=False) -> str:
        W, left, right = 1100, 92, 20
        n = len(NODE_ORDER)
        step = (W - left - right) / n
        lane_y = {"llm": 64, "hybrid": 150}
        H = 262
        out = [(f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Eighteen workflow nodes in two lanes, '
                f'coloured by the engine that ran them, with median seconds per node">')]
        # column index above, node names below the second lane's labels, angled down-right for room
        for i, name in enumerate(NODE_ORDER):
            x = left + step * (i + 0.5)
            out.append(svg_text(x, 22, f"{i + 1}", "svg-muted", "middle", 11, 600))
            out.append(f'<text transform="translate({x:.1f},{lane_y["hybrid"] + 46}) rotate(38)" class="svg-ink2" '
                       f'font-size="10.5" font-weight="500" text-anchor="start">{esc(NODE_LABEL[name])}</text>')
        for variant, y in lane_y.items():
            out.append(f'<line x1="{left}" y1="{y}" x2="{W - right}" y2="{y}" class="svg-lane" stroke-width="1"/>')
            out.append(svg_text(0, y - 12, "LLM only" if variant == "llm" else "LLM + Jev", "svg-ink", "start", 13, 800))
            out.append(svg_text(0, y + 7, f"{fmt_s(self.S[variant]['total_latency_ms']['median'])} · "
                                f"{fmt_usd(self.S[variant]['total_cost_usd'].get('median'))}", "svg-ink2", "start", 11, 500))
            for i, name in enumerate(NODE_ORDER):
                x = left + step * (i + 0.5)
                nd = self.node(variant, name)
                eng = self.engine_of(variant, name)
                if not nd:
                    out.append(f'<circle cx="{x:.1f}" cy="{y}" r="5" fill="none" class="svg-lane" stroke-width="1.5">'
                               f'<title>{esc(NODE_LABEL[name])}: did not run in this variant</title></circle>')
                    continue
                ms = nd["latency_ms"]["median"]
                r = 5 + min(13, (ms / 1000) ** 0.5 * 1.6)
                cls = {"llm": "svg-llm", "jev": "svg-jev", "mcp": "svg-mcp"}.get(eng, "svg-none")
                tip = (f"{NODE_LABEL[name]} — {eng}: median {fmt_s(ms)}, {fmt_usd(nd['cost_usd'].get('median'))}, "
                       f"{nd['executions']} executions")
                out.append(f'<circle cx="{x:.1f}" cy="{y}" r="{r:.1f}" class="{cls} svg-surface" stroke-width="2">'
                           f'<title>{esc(tip)}</title></circle>')
                if eng in ("llm", "jev"):
                    out.append(svg_text(x, y + 32, fmt_s(ms).replace(" ", ""), "svg-ink2", "middle", 10.5, 600))
        out.append("</svg>")
        return "".join(out)

    def race_figure(self) -> str:
        rows = []
        for name in NODE_ORDER:
            row = [NODE_LABEL[name]]
            for v in ("llm", "hybrid"):
                nd = self.node(v, name)
                row += [self.engine_of(v, name) or "–", fmt_s(nd["latency_ms"]["median"]) if nd else "–",
                        fmt_usd(nd["cost_usd"].get("median")) if nd else "–"]
            rows.append(row)
        return figure("One workflow, two engines",
                      "Every node of the Product Owner graph, in order. Circle area grows with median latency; "
                      "colour is the engine that ran it. Hover a node for its numbers.",
                      self.race(), legend([("llm", "LLM (Claude Haiku 4.5)"), ("jev", "Jev 1.13"),
                                          ("mcp", "MCP retrieval"), ("none", "deterministic")]),
                      table(["node", "baseline engine", "latency", "cost", "hybrid engine", "latency", "cost"], rows))

    # -- paired horizontal bars for the bounded nodes ---------------------------------------------
    def paired(self, metric: str) -> str:
        key, fmt, title, sub = {
            "latency": ("latency_ms", fmt_s, "Bounded decisions: time per step",
                        ("Median wall time per execution of each bounded node, three executions per engine. "
                         "Same questions, same thresholds; only the engine differs.")),
            "cost": ("cost_usd", fmt_usd, "Bounded decisions: cost per step",
                     "Median provider-reported cost per execution. Jev bills input tokens only, at $0.042 per million."),
        }[metric]
        vals = {}
        for name in BOUNDED:
            vals[name] = {v: (self.node(v, name) or {}).get(key, {}).get("median") for v in ("llm", "hybrid")}
        vmax = max(x for d in vals.values() for x in d.values() if x is not None)
        W, left, right, bh, gap, rowh = 1000, 210, 110, 14, 3, 48
        H = 10 + rowh * len(BOUNDED)
        plot = W - left - right
        out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">']
        for i, name in enumerate(BOUNDED):
            y = 10 + rowh * i
            out.append(svg_text(left - 14, y + bh + gap + 4, NODE_LABEL[name], "svg-ink", "end", 13, 600))
            for j, (v, cls, label) in enumerate((("llm", "svg-llm", "LLM"), ("hybrid", "svg-jev", "Jev"))):
                x = vals[name][v]
                yy = y + j * (bh + gap)
                if x is None:
                    continue
                w = plot * x / vmax
                out.append(bar(left, yy, w, bh, cls, f"{NODE_LABEL[name]} — {label}: {fmt(x)}"))
                out.append(svg_text(left + w + 8, yy + bh - 3, fmt(x), "svg-ink2", "start", 11.5, 600))
            a, b = vals[name]["llm"], vals[name]["hybrid"]
            if a and b:
                out.append(svg_text(W - 4, y + bh + gap + 4, f"{a / b:.0f}×", "svg-ink", "end", 13, 800))
        out.append(f'<line x1="{left}" y1="4" x2="{left}" y2="{H - 4}" class="svg-hair" stroke-width="1"/>')
        out.append("</svg>")
        rows = [[NODE_LABEL[n], fmt(vals[n]["llm"]), fmt(vals[n]["hybrid"]),
                 f"{vals[n]['llm'] / vals[n]['hybrid']:.1f}×" if vals[n]["llm"] and vals[n]["hybrid"] else "–"] for n in BOUNDED]
        return figure(title, sub, "".join(out), legend([("llm", "LLM"), ("jev", "Jev")]) ,
                      table(["node", "LLM", "Jev", "LLM ÷ Jev"], rows))

    # -- where a run's time and cost go --------------------------------------------------------------
    def composition(self) -> str:
        W, left, right, bh = 1000, 110, 90, 22
        plot = W - left - right
        parts = []
        rows = []
        y = 8
        for metric, key, fmt, label in (("time", "latency_ms", fmt_s, "Time"), ("cost", "cost_usd", fmt_usd, "Cost")):
            totals = {v: sum((self.S[v]["engines"].get(e, {}).get(key, {}) or {}).get("median") or 0
                             for e in ("llm", "jev", "mcp")) for v in ("llm", "hybrid")}
            vmax = max(totals.values())
            parts.append(svg_text(0, y + 15, label, "svg-ink", "start", 13, 800))
            for v in ("llm", "hybrid"):
                parts.append(svg_text(left - 12, y + bh - 6, "LLM only" if v == "llm" else "LLM + Jev", "svg-ink2", "end", 12, 600))
                x = left
                for e, cls in (("llm", "svg-llm"), ("jev", "svg-jev"), ("mcp", "svg-mcp")):
                    val = (self.S[v]["engines"].get(e, {}).get(key, {}) or {}).get("median") or 0
                    if not val:
                        continue
                    w = plot * val / vmax
                    parts.append(bar(x, y, w, bh, cls, f"{v}: {e} {fmt(val)}"))
                    rows.append([label, v, e, fmt(val)])
                    x += w + 2
                parts.append(svg_text(x + 6, y + bh - 6, fmt(totals[v]), "svg-ink2", "start", 11.5, 600))
                y += bh + 8
            y += 18
        svg = f'<svg viewBox="0 0 {W} {y}" role="img" aria-label="Where each run\'s time and cost go, by engine">' + "".join(parts) + "</svg>"
        return figure("Where a whole run's time and cost go",
                      "One complete run per variant. In the hybrid, the eight generative LLM steps account for almost "
                      "all of what remains; Jev's 35 calls are the thin orange slice.",
                      svg, legend([("llm", "LLM calls"), ("jev", "Jev calls"), ("mcp", "MCP retrieval")]),
                      table(["measure", "variant", "engine", "median per run"], rows))

    # -- consistency dot plot -----------------------------------------------------------------------
    def consistency(self) -> str:
        cons = self.c.get("consistency", {})
        if not cons.get("llm"):
            return ""
        lo = 0.85
        W, left, right, rowh = 1000, 210, 40, 40
        H = 40 + rowh * len(BOUNDED)
        plot = W - left - right
        def X(p):
            return left + plot * (max(p, lo) - lo) / (1 - lo)
        out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Decision agreement per bounded node and engine">']
        for t in (0.85, 0.90, 0.95, 1.0):
            out.append(f'<line x1="{X(t):.1f}" y1="22" x2="{X(t):.1f}" y2="{H - 18}" class="svg-hair" stroke-width="1"/>')
            out.append(svg_text(X(t), 14, f"{t * 100:.0f} %", "svg-muted", "middle", 11, 500))
        rows = []
        for i, name in enumerate(BOUNDED):
            y = 40 + rowh * i
            out.append(svg_text(left - 14, y + 4, NODE_LABEL[name], "svg-ink", "end", 13, 600))
            a = cons["llm"].get(name, {}).get("decision_agreement")
            b = cons["jev"].get(name, {}).get("decision_agreement")
            if a is not None and b is not None and abs(a - b) > 1e-9:
                out.append(f'<line x1="{X(a):.1f}" y1="{y}" x2="{X(b):.1f}" y2="{y}" class="svg-hair" stroke-width="2"/>')
            for val, cls, label in ((a, "svg-llm", "LLM"), (b, "svg-jev", "Jev")):
                if val is None:
                    continue
                out.append(f'<circle cx="{X(val):.1f}" cy="{y}" r="7" class="{cls} svg-surface" stroke-width="2">'
                           f'<title>{esc(NODE_LABEL[name])} — {label}: {val * 100:.1f} % of decisions reproduced</title></circle>')
            if b is not None and b < 1:
                out.append(svg_text(X(b) - 12, y + 4, f"{b * 100:.1f} %", "svg-ink2", "end", 11.5, 600))
            rows.append([NODE_LABEL[name], f"{a * 100:.1f} %" if a is not None else "–",
                         f"{b * 100:.1f} %" if b is not None else "–",
                         ", ".join(u["decision"].split("[")[-1].rstrip("]") if "[" in u["decision"] else u["decision"]
                                   for u in cons["jev"].get(name, {}).get("unstable", [])[:3]) or "–"])
        out.append("</svg>")
        reps = cons.get("repeats", "n")
        return figure("Consistency: identical input, same decision?",
                      f"Each bounded node replayed {reps}× on one frozen input with each engine; share of individual "
                      f"decisions (after thresholds) that matched the modal decision. Axis starts at 85 %.",
                      "".join(out), legend([("llm", "LLM at temperature 0"), ("jev", "Jev 1.13")]),
                      table(["node", "LLM", "Jev", "Jev decisions that flipped"], rows))

    # -- quality per dimension -------------------------------------------------------------------------
    def quality(self) -> str:
        q = self.c.get("quality", {})
        if not q.get("judgements"):
            return ""
        dims = list(DIM_LABEL)
        W, left, right, bh, gap, rowh = 1000, 250, 60, 11, 3, 36
        H = 24 + rowh * len(dims)
        plot = W - left - right
        out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Blind judge scores per rubric dimension">']
        for t in (1, 2, 3, 4, 5):
            x = left + plot * t / 5
            out.append(f'<line x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="{H - 6}" class="svg-hair" stroke-width="1"/>')
            out.append(svg_text(x, 12, str(t), "svg-muted", "middle", 11, 500))
        rows = []
        for i, d in enumerate(dims):
            y = 24 + rowh * i
            out.append(svg_text(left - 14, y + bh + gap + 2, DIM_LABEL[d], "svg-ink", "end", 12.5, 600))
            vals = {}
            for j, (v, cls, label) in enumerate((("llm", "svg-llm", "LLM only"), ("hybrid", "svg-jev", "LLM + Jev"))):
                m = q["per_dimension"][v][d]["mean"]
                vals[v] = m
                out.append(bar(left, y + j * (bh + gap), plot * m / 5, bh, cls, f"{DIM_LABEL[d]} — {label}: {m:.1f} / 5"))
            if vals["llm"] != vals["hybrid"]:
                out.append(svg_text(W - 4, y + bh + gap + 2, f"{vals['hybrid'] - vals['llm']:+.1f}".replace("-", "−"),
                                    "svg-ink2", "end", 12, 700))
            rows.append([DIM_LABEL[d], f"{vals['llm']:.1f}", f"{vals['hybrid']:.1f}"])
        out.append("</svg>")
        tot = q["total_score"]
        return figure("PRD quality, judged blind",
                      f"{q['judgements']} judgements by {q['judge_model']} (each pair scored in both A/B orders), "
                      f"1–5 per dimension. Totals out of 50: LLM only {tot['llm']['mean']:.1f}, "
                      f"LLM + Jev {tot['hybrid']['mean']:.1f}.",
                      "".join(out), legend([("llm", "LLM only"), ("jev", "LLM + Jev")]),
                      table(["dimension", "LLM only", "LLM + Jev"], rows))

    # -- landing hero -------------------------------------------------------------------------------
    def hero(self) -> str:
        S = self.S
        t_a, t_b = S["llm"]["total_latency_ms"]["median"], S["hybrid"]["total_latency_ms"]["median"]
        c_a, c_b = S["llm"]["total_cost_usd"].get("median"), S["hybrid"]["total_cost_usd"].get("median")
        bl_a = sum(self.node("llm", n)["latency_ms"]["median"] for n in BOUNDED)
        bl_b = sum(self.node("hybrid", n)["latency_ms"]["median"] for n in BOUNDED)
        bc_a = sum(self.node("llm", n)["cost_usd"]["median"] for n in BOUNDED)
        bc_b = sum(self.node("hybrid", n)["cost_usd"]["median"] for n in BOUNDED)
        kpis = [
            (pct(t_a, t_b), "end-to-end time for one PRD", f"{fmt_s(t_a)} → {fmt_s(t_b)}"),
            (pct(c_a, c_b), "cost for one PRD", f"{fmt_usd(c_a)} → {fmt_usd(c_b)}"),
            (pct(bl_a, bl_b), "time in the seven bounded steps", f"{fmt_s(bl_a)} → {fmt_s(bl_b)}"),
            (pct(bc_a, bc_b), "cost of the seven bounded steps", f"{fmt_usd(bc_a)} → {fmt_usd(bc_b)}"),
        ]
        tiles = "".join(f'<div class="kpi"><b class="{"up" if v.startswith("−") else ""}">{esc(v)}</b>'
                        f'<span>{esc(label)}</span><small>{esc(detail)}</small></div>' for v, label, detail in kpis)
        return (f'<section class="hero"><h1>What changes when an agent\'s decisions move from an LLM to Jev</h1>'
                f'<p class="lede">One Product Owner workflow turns an intent document into a PRD in eighteen steps. '
                f'We ran it twice on the same input: once with an LLM making every decision, once with Jev, a '
                f'decision model, taking the seven bounded ones. Every number here was measured.</p>'
                f'{self.race_figure()}<div class="kpis">{tiles}</div>'
                f'<p class="hero-note">Experiment {esc(self.c["experiment_id"])}: one complete run per variant on the '
                f'bundled intent; per-step medians over three executions. Consistency and judged quality are in the '
                f'<a href="executive-summary.html">executive summary</a>.</p></section>')


# --- the graph figure -------------------------------------------------------------------------------
# An explorable diagram of the compiled graph (docs/data/graph.json): one column of nodes top to
# bottom, loop bodies to the right, the knowledge server to the left. Click a box, or a row of the
# node table, for what it does and where it is implemented. The prose comes from the node table in
# docs/graph.md, so there is one place to edit it.

GRAPH_DATA = DOCS / "data" / "graph.json"
GW, GX, GWID, GH, GPITCH = 1120, 420, 280, 58, 104   # canvas width, column x, box width/height, row pitch
SIDE_X, SIDE_W, LEFT_X = 850, 240, 50
KIND_OF_TYPE = {"generative": "gen", "bounded": "bounded", "retrieval": "mcp", "deterministic": "det"}
KIND_CAPTION = {"gen": "generative · LLM", "bounded": "bounded · LLM or Jev", "mcp": "retrieval · MCP",
                "det": "deterministic · code", "file": "file"}
KIND_WORD = {"gen": "Generative: the LLM writes this in both variants.",
             "bounded": "Bounded decision: typed questions with a fixed answer space. The LLM answers them in "
                        "the baseline, Jev in the hybrid; thresholds live in config, not in the node.",
             "mcp": "Retrieval: the node calls the enterprise knowledge MCP server; no model is involved.",
             "det": "Deterministic: plain code, no inference.",
             "file": "A file on disk."}
LANES = {"parse_intent": "Understand the intent", "retrieve_constraints": "Constraints",
         "discover_risks": "Risks", "generate_requirements": "Requirements", "construct_prd": "The PRD"}
# what passes along the plain edges; conditional edges are labelled from the loop caps
EDGE_LABELS = {
    ("intent", "parse_intent"): "the intent, as written",
    ("parse_intent", "retrieve_knowledge"): "the brief",
    ("select_capabilities", "select_personas"): "selected capabilities and channels",
    ("select_personas", "identify_outcomes"): "impacted personas, rated",
    ("identify_outcomes", "retrieve_constraints"): "outcomes with signals",
    ("retrieve_constraints", "assess_constraints"): "candidate policies, features, services",
    ("assess_constraints", "discover_risks"): "applicable policies, overlapping features",
    ("discover_risks", "assess_risks"): "risks, unassessed",
    ("generate_requirements", "classify_requirements"): "candidate requirements",
    ("classify_requirements", "check_coverage"): "typed, prioritised, deduplicated",
    ("construct_prd", "validate_prd"): "the PRD, in Markdown",
    ("finalise", "prd"): "front matter added, results frozen",
}
QUESTIONS_OF = {
    "select_capabilities": ("capability_questions", ["capability_selected"]),
    "select_personas": ("persona_questions", ["persona_selected"]),
    "assess_constraints": ("constraint_questions", ["policy_applies", "feature_overlaps"]),
    "assess_risks": ("risk_questions, level_value", ["risk_investigate", "risk_priority_investigate"]),
    "classify_requirements": ("requirement_questions, rank_requirements", ["feature_overlaps"]),
    "check_coverage": ("coverage_questions", ["outcome_delivered"]),
    "validate_prd": ("validation_questions, PRD_CHECKS, BLOCKING_CHECKS", ["validation_pass"]),
}
GATES = {"assess_risks": ("after_assess_risks", "risk_attempts"),
         "check_coverage": ("after_check_coverage", "requirement_attempts"),
         "validate_prd": ("after_validate_prd", "prd_attempts")}
PSEUDO = {
    "intent": {"title": "Intent document", "kind": "file",
               "what": "A product intent in plain Markdown: the one input to a run.",
               "impl": ["examples/example-intent.md · the bundled intent",
                        "po_agent/cli.py · run --intent, benchmark --intent"]},
    "knowledge": {"title": "Enterprise knowledge", "kind": "file",
                  "what": "The MCP server over the catalogue: personas, capabilities, channels, policies, "
                          "existing features, services. Authoritative data comes from here, never from a model.",
                  "impl": [("po_agent/knowledge/server.py · get_capabilities, get_channels, get_personas, "
                            "search_policies, get_existing_features, get_services"),
                           "po_agent/knowledge/data/catalogue.yaml · the catalogue",
                           "po_agent/knowledge/client.py · MCPKnowledge, StaticKnowledge"]},
    "prd": {"title": "PRD and node results", "kind": "file",
            "what": "What a run leaves behind: the PRD, per-node results with engine, tokens, cost, latency and "
                    "decision evidence, the final state, and the trace id.",
            "impl": ["po_agent/runner.py · run_variant, write_results",
                     "results/<experiment>/<run>/ · prd.md, node-results.json, metrics.json, state.json, trace-id"]},
}


def inline_code(s: str) -> str:
    """Escape a table cell and turn `code` and *emphasis* spans into <code> and <em>."""
    return re.sub(r"\*([^*]+)\*", r"<em>\1</em>", re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(s)))


def node_table(md: str) -> dict[str, dict]:
    """The node table of docs/graph.md as {name: {column: cell}}."""
    section = md.split("## Node table", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")]   # header + data; not |---|
    headers = [h.strip() for h in rows[0].strip("|").split("|")]
    out = {}
    for line in rows[1:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        row = dict(zip(headers, cells))
        out[row["Node"].strip("`")] = row
    return out


class GraphFigure:
    def __init__(self, spec: dict, md: str):
        self.spec = spec
        self.table = node_table(md)
        self.loops = spec["loops"]
        self.types = {n["name"]: n["type"] for n in spec["nodes"]}
        self.cond = [e for e in spec["edges"] if e["branch"]]
        self.side = {e["target"]: e["source"] for e in self.cond if e["branch"] != "proceed"}  # loop body -> gate
        self.main = ["intent"] + [n["name"] for n in spec["nodes"] if n["name"] not in self.side] + ["prd"]
        # execution order for prev/next in the drawer
        self.order = []
        for n in self.main:
            self.order.append(n)
            if n == "retrieve_knowledge":
                self.order.append("knowledge")
            self.order += [b for b, gate in self.side.items() if gate == n]
        self.box: dict[str, tuple[float, float, float, float]] = {}
        for i, n in enumerate(self.main):
            self.box[n] = (GX, 70 + i * GPITCH, GWID, GH)
        for body, gate in self.side.items():
            self.box[body] = (SIDE_X, self.box[gate][1], SIDE_W, GH)
        self.box["knowledge"] = (LEFT_X, self.box["retrieve_knowledge"][1], SIDE_W, GH)
        self.height = 70 + len(self.main) * GPITCH - GPITCH + GH + 24

    # -- text --------------------------------------------------------------------------------------
    def kind(self, n: str) -> str:
        return PSEUDO[n]["kind"] if n in PSEUDO else KIND_OF_TYPE[self.types[n]]

    def title(self, n: str) -> str:
        if n in PSEUDO:
            return PSEUDO[n]["title"]
        label = NODE_LABEL[n]
        return label[0].upper() + label[1:]

    def what(self, n: str) -> str:
        return PSEUDO[n]["what"] if n in PSEUDO else self.table[n]["Responsibility"]

    def impl(self, n: str) -> list[str]:
        if n in PSEUDO:
            return PSEUDO[n]["impl"]
        t = self.types[n]
        out = [f"po_agent/graph/nodes.py · Nodes.{n}"]
        if t == "generative":
            out.append(f"po_agent/graph/prompts/{n}.md · the prompt (system.md is shared)")
            out.append("po_agent/engines/openrouter_llm.py · OpenRouterLLM.generate"
                       + (" (Markdown, prd_max_tokens)" if n in ("construct_prd", "refine_prd") else " (strict JSON schema)"))
        elif t == "bounded":
            fn, thresholds = QUESTIONS_OF[n]
            out.append(f"po_agent/graph/questions.py · {fn}")
            out.append("po_agent/engines/llm_decider.py · LLMDecisionEngine (baseline) · po_agent/engines/jev.py · JevEngine (hybrid)")
            out.append("po_agent/config.py · Thresholds." + ", ".join(thresholds))
        elif t == "retrieval":
            tools = self.table[n]["MCP"].replace("`", "")
            out.append(f"po_agent/knowledge/server.py · {tools}")
            out.append("po_agent/knowledge/client.py · MCPKnowledge.call")
        if n in GATES:
            fn, cap = GATES[n]
            out.append(f"po_agent/graph/build.py · {fn} (the gate) · po_agent/config.py · Loops.{cap} = {self.loops[cap]}")
        return out

    def eyebrow(self, n: str) -> str:
        k = self.kind(n)
        if k == "bounded":
            return "bounded · LLM in the baseline, Jev in the hybrid"
        if k == "gen":
            return "generative · LLM in both variants"
        return KIND_CAPTION[k]

    def data(self) -> dict:
        out = {}
        for n in self.order:
            row = self.table.get(n)
            d = {"title": self.title(n), "name": n if n not in PSEUDO else "", "kind": self.kind(n),
                 "eyebrow": self.eyebrow(n), "what": inline_code(self.what(n)),
                 "why": inline_code(row["Why this engine"]) if row else KIND_WORD[self.kind(n)],
                 "impl": self.impl(n)}
            if row:
                d["reads"] = inline_code(row["Input (from state)"])
                d["writes"] = inline_code(row["Output (to state)"])
                d["telemetry"] = inline_code(row["Telemetry"])
            out[n] = d
        return out

    # -- geometry ----------------------------------------------------------------------------------
    def centre(self, n):
        x, y, w, h = self.box[n]
        return x + w / 2, y + h / 2

    def cond_label(self, source: str, branch: str) -> str:
        cap = self.loops[GATES[source][1]]
        return {
            ("assess_risks", "investigate"): f"flagged, attempt < {cap}",
            ("assess_risks", "proceed"): "nothing flagged, or the cap reached",
            ("check_coverage", "refine"): f"gaps, attempt < {cap}",
            ("check_coverage", "proceed"): "covered, or the cap reached",
            ("validate_prd", "refine"): f"failed, attempt < {cap}",
            ("validate_prd", "proceed"): "passed, or the cap reached",
        }.get((source, branch), branch)

    def back_label(self, body: str) -> str:
        return {"investigate_risks": "mitigated, restated", "refine_requirements": "merged requirements",
                "refine_prd": "rewritten"}.get(body, "")

    def edges(self) -> list[str]:
        out = []

        def path(d, label=None, lx=0, ly=0, anchor="start", dash=False, arrow=True):
            cls = "g-edge dash" if dash else "g-edge"
            out.append(f'<path class="{cls}" d="{d}" fill="none"{" marker-end=\'url(#g-ah)\'" if arrow else ""}></path>')
            if label:
                out.append(f'<text class="g-elabel" x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}">{esc(label)}</text>')

        # the column: every edge between consecutive main nodes, conditional ones labelled from the loop caps
        plain = {(e["source"], e["target"]) for e in self.spec["edges"] if not e["branch"]}
        for a, b in zip(self.main, self.main[1:]):
            x, y, w, h = self.box[a]
            y2 = self.box[b][1]
            cx = x + w / 2
            cond = next((e for e in self.cond if e["source"] == a and e["target"] == b), None)
            if cond:
                label = self.cond_label(a, cond["branch"])
            elif (a, b) in plain or a == "intent" or b == "prd":
                label = EDGE_LABELS.get((a, b))
            else:
                raise ValueError(f"no edge {a} -> {b} in the compiled graph")
            path(f"M{cx:.1f},{y + h} L{cx:.1f},{y2}", label, cx + 8, (y + h + y2) / 2 + 4)
        # loops: out to the body on the gate's row, back on the row below it (or up to an earlier node)
        for body, gate in self.side.items():
            gx, gy, gw, gh = self.box[gate]
            bx, by, bw, _ = self.box[body]
            cy = gy + gh / 2
            branch = next(e["branch"] for e in self.cond if e["target"] == body)
            path(f"M{gx + gw},{cy - 10} L{bx},{cy - 10}", self.cond_label(gate, branch),
                 (gx + gw + bx) / 2, cy - 16, "middle")
            back = next(e["target"] for e in self.spec["edges"] if e["source"] == body)
            if back == gate:
                path(f"M{bx},{cy + 10} L{gx + gw},{cy + 10}", self.back_label(body), (gx + gw + bx) / 2, cy + 24, "middle")
            else:
                tx, ty, tw, th = self.box[back]
                tcy = ty + th / 2
                mx = bx + bw / 2
                path(f"M{mx},{by} L{mx},{tcy} L{tx + tw},{tcy}", self.back_label(body), mx + 8, (by + tcy) / 2 + 4)
        # the knowledge server feeds both retrieval nodes
        kx, ky, kw, kh = self.box["knowledge"]
        rx, ry, _, rh = self.box["retrieve_knowledge"]
        path(f"M{kx + kw},{ry + rh / 2} L{rx},{ry + rh / 2}", "the catalogue",
             (kx + kw + rx) / 2, ry + rh / 2 - 8, "middle", dash=True)
        _, cy2 = self.centre("retrieve_constraints")
        path(f"M{kx + kw / 2},{ky + kh} L{kx + kw / 2},{cy2} L{rx},{cy2}", "policies · features · services",
             kx + kw / 2 + 8, (ky + kh + cy2) / 2 + 4, dash=True)
        return out

    def nodes(self) -> list[str]:
        out = []
        for n in self.order:
            x, y, w, h = self.box[n]
            k = self.kind(n)
            aria = f"{self.title(n)}: {re.sub(r'`', '', self.what(n))}"
            out.append(f'<g class="g-node k-{k}" data-id="{n}" tabindex="0" role="button" aria-haspopup="dialog" '
                       f'aria-label="{esc(aria)}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9"></rect>'
                       f'<text class="title" x="{x + 12}" y="{y + 24}">{esc(self.title(n))}</text>'
                       f'<text class="kind" x="{x + 12}" y="{y + 44}">{esc(KIND_CAPTION[k])}</text></g>')
        return out

    def lanes(self) -> list[str]:
        return [f'<text class="g-lane" x="{GX - 16}" y="{self.box[n][1] - 12}" text-anchor="end">{esc(label)}</text>'
                for n, label in LANES.items() if n in self.box]

    def svg(self) -> str:
        return (f'<svg id="g-svg" viewBox="0 0 {GW} {self.height}" role="img" aria-label="The Product Owner graph, '
                f'top to bottom: {len(self.types)} nodes, three gated loops to the right, the knowledge server to '
                f'the left. Click a box for what it does and where it is implemented.">'
                '<defs><marker id="g-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="currentColor"></path></marker></defs>'
                + "".join(self.lanes()) + "".join(self.edges()) + "".join(self.nodes()) + "</svg>")

    def html(self) -> str:
        bar = ('<div class="g-bar">'
               '<span class="k-gen"><i></i>generative: the LLM writes, in both variants</span>'
               '<span class="k-bounded"><i></i>bounded: LLM in the baseline, Jev in the hybrid</span>'
               '<span class="k-mcp"><i></i>retrieval over MCP</span>'
               '<span class="k-det"><i></i>deterministic code</span>'
               '<span class="k-file"><i></i>a file</span>'
               '<span class="g-zoom"><label for="g-zoom">Zoom</label>'
               '<input type="range" id="g-zoom" min="35" max="130" value="100" aria-label="Zoom the diagram">'
               '<button type="button" id="g-fit">Fit</button><button type="button" id="g-full">100 %</button></span></div>')
        drawer = ('<div class="g-scrim" id="g-scrim"></div>'
                  '<aside class="g-drawer" id="g-drawer" role="dialog" aria-modal="false" aria-labelledby="g-dtitle" aria-hidden="true">'
                  '<div class="stripe"></div>'
                  '<div class="head"><div><div class="eyebrow" id="g-dkind"></div><h2 id="g-dtitle"></h2><code id="g-dname"></code></div>'
                  '<button type="button" class="close" id="g-close" aria-label="Close"><svg width="16" height="16" viewBox="0 0 16 16" '
                  'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
                  '<path d="M3 3l10 10M13 3L3 13"></path></svg></button></div>'
                  '<div class="body"><div><p class="what" id="g-dwhat"></p><p class="kindword" id="g-dwhy"></p></div>'
                  '<div id="g-dstate"><h3>Reads, then writes</h3><p class="io" id="g-dreads"></p><p class="io" id="g-dwrites"></p></div>'
                  '<div><h3>Implemented by</h3><ul id="g-dimpl"></ul></div>'
                  '<div id="g-dtel"><h3>Telemetry</h3><p class="io" id="g-dtelemetry"></p></div></div>'
                  '<div class="nav"><button type="button" id="g-prev"><span>Before</span><em id="g-prevt"></em></button>'
                  '<button type="button" id="g-next"><span>After</span><em id="g-nextt"></em></button></div></aside>')
        script = GRAPH_JS.replace("__DATA__", json.dumps(self.data())).replace("__ORDER__", json.dumps(self.order)) \
            .replace("__W__", str(GW)).replace("__H__", str(self.height))
        return (f'<div class="g-wide">{bar}<div class="g-fig" id="g-fig">{self.svg()}</div></div>'
                f'{drawer}<script>{script}</script>')


GRAPH_JS = r"""
(function () {
  var DATA = __DATA__, ORDER = __ORDER__, W = __W__, H = __H__;
  var body = document.body, drawer = document.getElementById("g-drawer"), fig = document.getElementById("g-fig");
  var current = null, opener = null;
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); }
  function implHtml(i) { var p = i.split(" · "); return p.length > 1 ? "<b>" + esc(p[0]) + "</b> · " + esc(p.slice(1).join(" · ")) : "<b>" + esc(i) + "</b>"; }
  function show(id, from) {
    var n = DATA[id]; if (!n) return;
    current = id; if (from) opener = from;
    document.querySelectorAll(".is-lit").forEach(function (el) { el.classList.remove("is-lit"); });
    document.querySelectorAll('[data-id="' + id + '"]').forEach(function (el) { el.classList.add("is-lit"); });
    drawer.className = "g-drawer k-" + n.kind;
    document.getElementById("g-dkind").textContent = n.eyebrow;
    document.getElementById("g-dtitle").textContent = n.title;
    document.getElementById("g-dname").textContent = n.name;
    document.getElementById("g-dname").style.display = n.name ? "" : "none";
    document.getElementById("g-dwhat").innerHTML = n.what;
    document.getElementById("g-dwhy").innerHTML = n.why;
    document.getElementById("g-dstate").style.display = n.reads ? "" : "none";
    document.getElementById("g-dreads").innerHTML = n.reads ? "<span>reads</span> " + n.reads : "";
    document.getElementById("g-dwrites").innerHTML = n.writes ? "<span>writes</span> " + n.writes : "";
    document.getElementById("g-dimpl").innerHTML = n.impl.map(function (i) { return "<li>" + implHtml(i) + "</li>"; }).join("");
    document.getElementById("g-dtel").style.display = n.telemetry ? "" : "none";
    document.getElementById("g-dtelemetry").innerHTML = n.telemetry || "";
    var k = ORDER.indexOf(id);
    var p = ORDER[(k + ORDER.length - 1) % ORDER.length], nx = ORDER[(k + 1) % ORDER.length];
    document.getElementById("g-prevt").textContent = DATA[p].title;
    document.getElementById("g-nextt").textContent = DATA[nx].title;
    document.getElementById("g-prev").onclick = function () { show(p); };
    document.getElementById("g-next").onclick = function () { show(nx); };
    drawer.setAttribute("aria-hidden", "false");
    body.classList.add("g-open");
    try { if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id); } catch (e) {}
    // bring the box into view inside the figure without moving the page (a table row may have opened it)
    var node = fig.querySelector('.g-node[data-id="' + id + '"]');
    if (node) {
      var r = node.getBoundingClientRect(), f = fig.getBoundingClientRect();
      if (r.top < f.top) fig.scrollTop -= f.top - r.top + 16; else if (r.bottom > f.bottom) fig.scrollTop += r.bottom - f.bottom + 16;
      if (r.left < f.left) fig.scrollLeft -= f.left - r.left + 16; else if (r.right > f.right) fig.scrollLeft += r.right - f.right + 16;
    }
  }
  function close() {
    body.classList.remove("g-open"); drawer.setAttribute("aria-hidden", "true");
    if (opener && opener.focus) opener.focus();
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
  }
  // delegated, so the node table further down the page opens the drawer too
  function target(ev) { var el = ev.target.closest ? ev.target.closest("[data-id]") : null; return el && DATA[el.getAttribute("data-id")] ? el : null; }
  document.addEventListener("click", function (ev) { var el = target(ev); if (el) show(el.getAttribute("data-id"), el); });
  document.addEventListener("keydown", function (ev) {
    var el = target(ev);
    if (el && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); show(el.getAttribute("data-id"), el); }
  });
  document.getElementById("g-close").addEventListener("click", close);
  document.getElementById("g-scrim").addEventListener("click", close);
  document.addEventListener("keydown", function (ev) {
    if (!body.classList.contains("g-open")) return;
    if (ev.key === "Escape") close();
    if (ev.key === "ArrowDown" || ev.key === "ArrowRight") { ev.preventDefault(); document.getElementById("g-next").click(); }
    if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") { ev.preventDefault(); document.getElementById("g-prev").click(); }
  });
  // zoom: the diagram is drawn W px wide; "Fit" shows the whole of it in the window's height
  var zoom = document.getElementById("g-zoom");
  function setZoom(pct) { zoom.value = pct; fig.style.setProperty("--w", (W * pct / 100) + "px"); }
  function fit() {
    var avail = Math.max(320, window.innerHeight - 40 - 26);
    setZoom(Math.max(35, Math.min(130, Math.floor(100 * avail / H))));
  }
  zoom.addEventListener("input", function () { setZoom(+zoom.value); });
  document.getElementById("g-fit").addEventListener("click", fit);
  document.getElementById("g-full").addEventListener("click", function () { setZoom(100); });
  setZoom(Math.max(35, Math.min(100, Math.floor(100 * (fig.clientWidth - 24) / W))));
  var h = (location.hash || "").slice(1);
  if (h && DATA[h]) show(h);
})();
"""

GRAPH_CSS = """
/* the graph figure: escapes .prose to the full content width */
.g-wide{width:calc(min(1120px,100vw) - 2rem);max-width:none;margin:1.2rem 0 2.2rem}
.g-bar{display:flex;gap:.6rem 1.3rem;flex-wrap:wrap;margin:0 0 .7rem;font-size:.86rem;color:var(--ink-2);align-items:center}
.g-bar i{display:inline-block;width:13px;height:13px;border-radius:3px;vertical-align:-2px;margin-right:.4rem;border:1.5px solid}
.g-bar .k-gen i{background:var(--g-gen-soft);border-color:var(--llm)}
.g-bar .k-bounded i{background:var(--g-bounded-soft);border-color:var(--jev)}
.g-bar .k-mcp i{background:var(--g-mcp-soft);border-color:var(--mcp)}
.g-bar .k-det i{background:var(--g-det-soft);border-color:var(--muted);border-style:dashed}
.g-bar .k-file i{background:var(--hair-2);border-color:var(--hair)}
.g-zoom{margin-left:auto;display:flex;align-items:center;gap:.5rem}
.g-zoom label{font-size:.86rem;color:var(--ink-2)}
.g-zoom input[type=range]{width:130px;accent-color:var(--llm)}
.g-zoom button,.g-drawer .nav button,.g-drawer .close{font:500 .86rem var(--font);color:var(--ink);background:var(--surface);border:1px solid var(--hair);border-radius:6px;padding:.3rem .6rem;cursor:pointer}
.g-zoom button:hover,.g-drawer .nav button:hover,.g-drawer .close:hover{background:var(--hair-2)}
.g-fig{background:var(--surface);border:1px solid var(--hair);border-radius:10px;padding:12px;overflow:auto;max-height:calc(100vh - 40px)}
.g-fig svg{display:block;width:var(--w,100%);max-width:none;height:auto;margin:0 auto;font-family:var(--font)}
.g-fig text{fill:var(--ink)}
.g-lane{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;fill:var(--muted)}
.g-edge{stroke:var(--muted);stroke-width:1.4;color:var(--muted)}
.g-edge.dash{stroke-dasharray:3 4;stroke-width:1.1}
.g-elabel{font-size:11px;fill:var(--ink-2)}
.g-node{cursor:pointer}
.g-node rect{stroke-width:1.5;transition:stroke-width .15s}
.g-node.k-gen rect{fill:var(--g-gen-soft);stroke:var(--llm)}
.g-node.k-bounded rect{fill:var(--g-bounded-soft);stroke:var(--jev);stroke-width:2}
.g-node.k-mcp rect{fill:var(--g-mcp-soft);stroke:var(--mcp)}
.g-node.k-det rect{fill:var(--g-det-soft);stroke:var(--muted);stroke-dasharray:4 3}
.g-node.k-file rect{fill:var(--hair-2);stroke:var(--hair)}
.g-node .title{font-size:14px;font-weight:700}
.g-node .kind{font-size:9.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;fill:var(--ink-2)}
.g-node.k-gen .kind{fill:var(--llm)} .g-node.k-bounded .kind{fill:var(--jev)} .g-node.k-mcp .kind{fill:var(--mcp)}
.g-node:hover rect{filter:brightness(.96)}
.g-node.is-lit rect,.g-node:focus-visible rect{stroke:var(--ink);stroke-width:3;stroke-dasharray:none}
.g-node:focus-visible{outline:none}
tbody tr[data-id]{cursor:pointer} tbody tr.is-lit td{background:var(--hair-2)} tbody tr.is-lit td:first-child{box-shadow:inset 4px 0 0 var(--jev)}
/* the drawer */
.g-scrim{position:fixed;inset:0;background:rgba(21,33,58,.28);opacity:0;pointer-events:none;transition:opacity .2s;z-index:20}
.g-drawer{position:fixed;top:0;right:0;bottom:0;width:min(460px,100%);background:var(--surface);box-shadow:0 0 0 1px var(--hair),-24px 0 60px -30px rgba(0,0,0,.45);transform:translateX(104%);transition:transform .28s cubic-bezier(.2,.8,.2,1);z-index:21;display:flex;flex-direction:column;font-size:.92rem}
body.g-open .g-scrim{opacity:1;pointer-events:auto}
body.g-open .g-drawer{transform:none}
.g-drawer .stripe{height:6px;background:var(--muted);flex:none}
.g-drawer.k-gen .stripe{background:var(--llm)} .g-drawer.k-bounded .stripe{background:var(--jev)} .g-drawer.k-mcp .stripe{background:var(--mcp)}
.g-drawer .head{display:flex;align-items:flex-start;gap:12px;padding:1.3rem 1.4rem 0}
.g-drawer .head>div{flex:1;min-width:0}
.g-drawer .eyebrow{font-size:.72rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);margin-bottom:.4rem}
.g-drawer.k-gen .eyebrow{color:var(--llm)} .g-drawer.k-bounded .eyebrow{color:var(--jev)}
.g-drawer h2{font-size:1.5rem;line-height:1.15;margin:0;padding:0;border:0;letter-spacing:-.02em}
.g-drawer .head code{display:inline-block;margin-top:.4rem;font-size:.8rem}
.g-drawer .close{flex:none;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;padding:0}
.g-drawer .body{padding:1.1rem 1.4rem 1.6rem;overflow-y:auto;display:grid;gap:1.3rem;align-content:start}
.g-drawer .what{font-size:1.05rem;line-height:1.5;margin:0}
.g-drawer .kindword{color:var(--ink-2);font-size:.9rem;margin:.4rem 0 0}
.g-drawer h3{font-size:.72rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:0 0 .5rem}
.g-drawer .io{margin:0 0 .3rem;color:var(--ink-2);font-size:.88rem;line-height:1.55}
.g-drawer .io span{display:inline-block;min-width:3.2em;font-weight:700;color:var(--ink)}
.g-drawer ul{margin:0;padding:0;list-style:none;display:grid;gap:.4rem}
.g-drawer li{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;line-height:1.45;padding:.45rem .7rem;background:var(--hair-2);border-radius:8px;overflow-wrap:anywhere;max-width:none}
.g-drawer li b{font-weight:600;color:var(--ink)}
.g-drawer.k-gen li b{color:var(--llm)} .g-drawer.k-bounded li b{color:var(--jev)}
.g-drawer .nav{display:flex;gap:.5rem;justify-content:space-between;padding:0 1.4rem 1.3rem;margin-top:auto}
.g-drawer .nav button{padding:.5rem .8rem;max-width:48%;text-align:left;border-radius:8px}
.g-drawer .nav button span{display:block;font-size:.68rem;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.g-drawer .nav button em{font-style:normal;font-weight:600}
.g-drawer .nav button:last-child{text-align:right}
@media (max-width:720px){
  .g-drawer{top:auto;left:0;width:100%;max-height:82vh;border-radius:16px 16px 0 0;transform:translateY(104%)}
  .g-zoom{margin-left:0}
}
"""

CSS += GRAPH_CSS


# --- rendering ----------------------------------------------------------------------------------


def example_figure(ex: dict) -> str:
    note = f'<p class="note">{inline_code(ex["note"])}</p>' if ex.get("note") else ""
    return (f'<figure class="code"><figcaption>{esc(ex["title"])}</figcaption>'
            f'<pre><code class="language-{esc(ex["lang"])}">{esc(ex["text"])}</code></pre>{note}</figure>')


def census_table(census: dict) -> str:
    rows = [[r["node"], r["per"], r["noul"] or "", r["choice"] or "", r["score"] or "", r["questions"], r["calls"]]
            for r in census["rows"]]
    tot = ["total", "", sum(r["noul"] for r in census["rows"]), sum(r["choice"] for r in census["rows"]),
           sum(r["score"] for r in census["rows"]), sum(r["questions"] for r in census["rows"]),
           sum(r["calls"] for r in census["rows"])]
    return table(["node", "one set per", "noul", "choice", "score", "questions", f"calls ({census['chunk']} per call)"],
                 rows + [tot])


def render(md: str, charts: Charts | None, graph: dict | None = None, examples: dict | None = None) -> str:
    src = md
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"\n\n@@MERMAID{len(blocks) - 1}@@\n\n"
    md = re.sub(r"```mermaid\n(.*?)```", stash, md, flags=re.DOTALL)

    htmls: list[str] = []

    def chart(m: re.Match) -> str:
        kind = m.group(1)
        if kind == "graph":
            htmls.append(GraphFigure(graph, src).html() if graph else "")
            return f"\n\n@@HTML{len(htmls) - 1}@@\n\n"
        if kind.startswith("example:") or kind == "census":
            if not examples:
                return ""
            if kind == "census":
                htmls.append(census_table(examples["census"]))
            else:
                figs = [example_figure(examples[k]) for k in kind.split(":", 1)[1].split("|")]
                htmls.append(f'<div class="pair">{"".join(figs)}</div>' if len(figs) > 1 else figs[0])
            return f"\n\n@@HTML{len(htmls) - 1}@@\n\n"
        if charts is None:
            return ""
        out = {"hero": charts.hero, "chart:race": charts.race_figure, "chart:latency": lambda: charts.paired("latency"),
               "chart:cost": lambda: charts.paired("cost"), "chart:composition": charts.composition,
               "chart:consistency": charts.consistency, "chart:quality": charts.quality}[kind]()
        htmls.append(out)
        return f"\n\n@@HTML{len(htmls) - 1}@@\n\n"
    md = re.sub(r"^\{\{([a-z:_|]+)\}\}\s*$", chart, md, flags=re.MULTILINE)

    body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"])
    for i, src in enumerate(blocks):
        body = body.replace(f"<p>@@MERMAID{i}@@</p>", f'<pre class="mermaid">{html.escape(src)}</pre>')
    for i, h in enumerate(htmls):
        body = body.replace(f"<p>@@HTML{i}@@</p>", h)
    if graph and "{{graph}}" in src:   # node-table rows open the same drawer as the boxes
        body = re.sub(r"<tr>(\s*<td>\d+</td>\s*<td><code>([a-z_]+)</code></td>)", r'<tr data-id="\2" tabindex="0">\1', body)
    return re.sub(r'href="([^":#]+)\.md(#[^"]*)?"', r'href="\1.html\2"', body)


def title_of(md: str, fallback: str) -> str:
    m = re.search(r"^# (.+)$", md, flags=re.MULTILINE)
    return m.group(1).strip() if m else fallback


def description_of(md: str) -> str:
    for line in md.splitlines():
        s = line.strip()
        if s and not s.startswith(("#", "{{", "|", "-", "*", "```", "!")):
            return re.sub(r"[*_`\[\]]", "", s)[:160]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    charts = Charts(json.loads(DATA.read_text())) if DATA.exists() else None
    graph = json.loads(GRAPH_DATA.read_text()) if GRAPH_DATA.exists() else None
    examples = json.loads(EXAMPLES.read_text()) if EXAMPLES.exists() else None
    pages = {p.stem: p for p in DOCS.glob("*.md")}
    names = [n for n in ORDER if n in pages] + sorted(n for n in pages if n not in ORDER)
    titles = {n: title_of(pages[n].read_text(), n.replace("-", " ").title()) for n in names}
    exp = charts.c["experiment_id"] if charts else "n/a"
    for n in names:
        src = pages[n].read_text()
        nav = "".join(f'<a class="item{" current" if m == n else ""}" href="{m}.html">{esc(NAV_TITLES.get(m, titles[m]))}</a>'
                      for m in names if m != "index")
        body = render(src, charts, graph, examples)
        if n != "index":
            body = f'<div class="prose">{body}</div>'
        footer = (f'Product Owner agent benchmark · experiment {esc(exp)} · '
                  f'<a href="https://github.com/SteFletcher/product-owner-agent-demo">source on GitHub</a>')
        (out / f"{n}.html").write_text(PAGE.format(title=esc(titles[n] if n != "index" else "Product Owner agent: LLM vs LLM + Jev"),
                                                    description=esc(description_of(src)), css=CSS, nav=nav, body=body,
                                                    footer=footer))
    for pattern in ("*.png", "*.jpg", "*.svg"):
        for extra in DOCS.glob(pattern):
            shutil.copy(extra, out / extra.name)
    (out / "data").mkdir(exist_ok=True)
    for data in (DATA, GRAPH_DATA, EXAMPLES):
        if data.exists():
            shutil.copy(data, out / "data" / data.name)
    (out / ".nojekyll").write_text("")
    print(f"built {len(names)} pages into {out}/: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
