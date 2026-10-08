"""Build docs/*.md into a static site: one design system, charts generated from the experiment's
own data (docs/data/comparison.json), Mermaid rendered client-side.

    python tools/builddocs.py [--out site]

Markdown pages may use placeholders, each on its own line:
    {{hero}}                 the landing hero (workflow race strip + headline deltas)
    {{chart:race}}           the two-lane workflow strip
    {{chart:latency}}        bounded nodes, median latency, LLM vs Jev
    {{chart:cost}}           bounded nodes, median cost, LLM vs Jev
    {{chart:composition}}    where a run's time and cost go, per variant
    {{chart:consistency}}    decision agreement per bounded node, per engine
    {{chart:quality}}        blind judge scores per rubric dimension
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
ORDER = ["index", "executive-summary", "architecture", "graph", "engine-selection", "configuration",
         "benchmark", "benchmark-results"]
NAV_TITLES = {"index": "Overview", "executive-summary": "Executive summary", "architecture": "Architecture",
              "graph": "The graph", "engine-selection": "Engine selection", "configuration": "Configuration",
              "benchmark": "Method", "benchmark-results": "Raw report"}

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
  --ok:#0a7f3f;--font:"Manrope",system-ui,-apple-system,"Segoe UI",sans-serif;}
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


# --- rendering ----------------------------------------------------------------------------------


def render(md: str, charts: Charts | None) -> str:
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"\n\n@@MERMAID{len(blocks) - 1}@@\n\n"
    md = re.sub(r"```mermaid\n(.*?)```", stash, md, flags=re.DOTALL)

    htmls: list[str] = []

    def chart(m: re.Match) -> str:
        if charts is None:
            return ""
        kind = m.group(1)
        out = {"hero": charts.hero, "chart:race": charts.race_figure, "chart:latency": lambda: charts.paired("latency"),
               "chart:cost": lambda: charts.paired("cost"), "chart:composition": charts.composition,
               "chart:consistency": charts.consistency, "chart:quality": charts.quality}[kind]()
        htmls.append(out)
        return f"\n\n@@HTML{len(htmls) - 1}@@\n\n"
    md = re.sub(r"^\{\{([a-z:]+)\}\}\s*$", chart, md, flags=re.MULTILINE)

    body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"])
    for i, src in enumerate(blocks):
        body = body.replace(f"<p>@@MERMAID{i}@@</p>", f'<pre class="mermaid">{html.escape(src)}</pre>')
    for i, h in enumerate(htmls):
        body = body.replace(f"<p>@@HTML{i}@@</p>", h)
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
    pages = {p.stem: p for p in DOCS.glob("*.md")}
    names = [n for n in ORDER if n in pages] + sorted(n for n in pages if n not in ORDER)
    titles = {n: title_of(pages[n].read_text(), n.replace("-", " ").title()) for n in names}
    exp = charts.c["experiment_id"] if charts else "n/a"
    for n in names:
        src = pages[n].read_text()
        nav = "".join(f'<a class="item{" current" if m == n else ""}" href="{m}.html">{esc(NAV_TITLES.get(m, titles[m]))}</a>'
                      for m in names if m != "index")
        body = render(src, charts)
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
    if DATA.exists():
        (out / "data").mkdir(exist_ok=True)
        shutil.copy(DATA, out / "data" / DATA.name)
    (out / ".nojekyll").write_text("")
    print(f"built {len(names)} pages into {out}/: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
