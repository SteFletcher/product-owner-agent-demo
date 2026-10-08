"""Write docs/data/examples.json: the real payloads behind docs/examples.md, taken from a recorded
experiment and the code that built them, so the page shows what was actually sent and answered.

    python tools/genexamples.py [--experiment results/<id>]   (default: the newest experiment)

What it records, all from <experiment>/hybrid/run-01 and llm/run-01 plus the question builders:
    intent                     the head of the intent document
    gen_request                the OpenRouter chat/completions body for parse_intent (strict JSON schema)
    gen_response               the Brief the model returned (from state.json)
    q_noul, q_choice, q_score  one real question of each System One type, as built and as sent
    a_noul, a_choice, a_score  Jev's normalised Answer to each
    jev_request                the POST /v1/systemone body for assess_risks, attempt 1, first risk
    jev_answers                Jev's answers to those four questions and the decision the node derived
    llm_request                the baseline's chat/completions body for the same four questions
    llm_answers                the baseline's answers to its own first risk, as the schema returns them
    census                     per bounded node: items, question types, questions, calls (chunks of 12),
                               built on the run's final state and checked against the node's last execution
Needs the package (po_agent) and a results directory; the docs build itself only reads the JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from po_agent.engines import llm_decider
from po_agent.engines.base import normalise
from po_agent.engines.openrouter_llm import strict_schema
from po_agent.graph import questions as Q
from po_agent.graph.nodes import SYSTEM, prompt
from po_agent.state import Answer, Brief, Capability, Catalogue, Feature, Outcome, Requirement, Risk

OUT = ROOT / "docs" / "data" / "examples.json"
CHUNK = 12


def pretty(o) -> str:
    return json.dumps(o, indent=1, ensure_ascii=False)


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n].rstrip() + f"\n… ({len(s) - n:,} more characters)"


def newest_experiment() -> Path:
    runs = sorted(p for p in (ROOT / "results").glob("*") if (p / "hybrid" / "run-01" / "state.json").exists())
    if not runs:
        sys.exit("no experiment with a hybrid/run-01/state.json under results/")
    return runs[-1]


def load(exp: Path, variant: str):
    run = exp / variant / "run-01"
    return json.loads((run / "state.json").read_text()), json.loads((run / "node-results.json").read_text())


def record(node_results: list[dict], node: str, attempt: int = 1) -> dict:
    return next(r for r in node_results if r["node"] == node and r["attempt"] == attempt)


def to_schema_answer(qid: str, a: dict) -> dict:
    """The baseline schema's shape for one recorded (normalised) answer: the inverse of to_raw."""
    if a["type"] == "noul":
        return {"id": qid, "p_yes": a["noul"], "probabilities": []}
    return {"id": qid, "p_yes": None, "probabilities": [{"option": k, "p": v} for k, v in a["probabilities"].items()]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--experiment", default=None)
    args = ap.parse_args(argv)
    exp = Path(args.experiment) if args.experiment else newest_experiment()
    hy_state, hy_nodes = load(exp, "hybrid")
    llm_state, llm_nodes = load(exp, "llm")
    jev_model = record(hy_nodes, "assess_risks")["model"]
    llm_model = record(llm_nodes, "parse_intent")["model"]
    ex: dict[str, dict] = {}

    def put(key, title, text, lang="json", note=None):
        ex[key] = {"title": title, "lang": lang, "text": text, **({"note": note} if note else {})}

    # --- the intent and a generative node -----------------------------------------------------------
    intent = (exp / "intent.md").read_text()
    put("intent", "examples/example-intent.md (the head)", clip(intent, 700), "markdown")
    body = {"model": llm_model,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": clip(prompt("parse_intent", intent=intent), 900)}],
            "temperature": 0.2, "max_tokens": 6000, "usage": {"include": True},
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "Brief", "strict": True, "schema": strict_schema(Brief)}}}
    put("gen_request", f"POST /api/v1/chat/completions · parse_intent · {llm_model}", pretty(body),
        note="The user message is the prompt template with the intent substituted; cut here for length.")
    put("gen_response", "The Brief the model returned (choices[0].message.content, validated against the schema)",
        pretty(hy_state["brief"]))

    # --- the three question types, as built and as sent ---------------------------------------------
    brief = Brief(**hy_state["brief"])
    cat = Catalogue(**hy_state["catalogue"])
    risks1 = [Risk(**{**r, "mitigation": None}) for r in hy_state["risks"]]   # attempt 1: nothing mitigated yet
    _, cap_qs = Q.capability_questions(brief, cat.capabilities, cat.channels)
    risk_state, risk_qs = Q.risk_questions(brief, risks1)
    first_cap = f"cap:{cat.capabilities[0].id}"
    r0 = risks1[0].id
    hy_ans = record(hy_nodes, "assess_risks")["output"]["evidence"]["answers"]
    cap_ans = record(hy_nodes, "select_capabilities")["output"]["evidence"]["answers"]
    put("q_noul", f"noul · questions.py capability_questions → {first_cap}", pretty({first_cap: cap_qs[first_cap]}))
    put("q_choice", f"choice · questions.py risk_questions → cat:{r0}", pretty({f"cat:{r0}": risk_qs[f"cat:{r0}"]}))
    put("q_score", f"score · questions.py risk_questions → lik:{r0}", pretty({f"lik:{r0}": risk_qs[f"lik:{r0}"]}))
    put("a_noul", "Jev's answer, normalised (Answer)", pretty(cap_ans[first_cap]))
    put("a_choice", "Jev's answer, normalised (Answer)", pretty(hy_ans[f"cat:{r0}"]))
    put("a_score", "Jev's answer, normalised (Answer)", pretty(hy_ans[f"lik:{r0}"]))

    # --- one bounded node, both ways: assess_risks, attempt 1, the first risk ------------------------
    four = {k: v for k, v in risk_qs.items() if k.endswith(":" + r0)}
    n_calls = -(-len(risk_qs) // CHUNK)
    jev_body = {"model": jev_model, "state": risk_state, "questions": four}
    put("jev_request", f"POST /api/v1/systemone · assess_risks · {jev_model}", pretty(jev_body),
        note=(f"`state` goes over the wire as one JSON string (shown parsed). The node asked {len(risk_qs)} questions "
              f"for {len(risks1)} risks, {CHUNK} per call, so {n_calls} calls; these are the four for the first risk."))
    answers = {k: Answer(**hy_ans[k]) for k in four}
    cat_a, lik, imp, inv = (answers[f"{k}:{r0}"] for k in ("cat", "lik", "imp", "inv"))
    likelihood, impact = Q.level_value(lik.probabilities), Q.level_value(imp.probabilities)
    priority = round(likelihood * impact, 2)
    derived = {"category": cat_a.choice, "likelihood": likelihood, "impact": impact, "priority": priority,
               "p_needs_investigation": inv.noul,
               "needs_investigation": inv.noul >= 0.6 or priority >= 12,
               "rule": "needs_investigation = p ≥ thresholds.risk_investigate (0.6) or priority ≥ "
                       "thresholds.risk_priority_investigate (12); likelihood and impact are expected values on 1–5"}
    put("jev_answers", "Jev's four answers (response.answers, normalised) and what the node derived",
        pretty({"answers": {k: a.model_dump(exclude_none=True, exclude_defaults=True) for k, a in answers.items()},
                "derived": derived}))
    rec = record(hy_nodes, "assess_risks")
    ex["jev_answers"]["note"] = (f"The whole node: {rec['calls']} calls, {rec['input_tokens']:,} input tokens, "
                                 f"{rec['output_tokens']:,} output tokens, ${rec['cost_usd']:.4f}, {rec['latency_ms'] / 1000:.1f} s.")

    llm_body = {"model": llm_model,
                "messages": [{"role": "system", "content": llm_decider.SYSTEM},
                             {"role": "user", "content": llm_decider.render(risk_state, four)}],
                "temperature": 0.0, "max_tokens": 4000, "usage": {"include": True},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "DecisionAnswers", "strict": True, "schema": strict_schema(llm_decider.DecisionAnswers)}}}
    put("llm_request", f"POST /api/v1/chat/completions · assess_risks · {llm_model}", pretty(llm_body),
        note="The same state and the same four questions, rendered as text, with one constant answer schema.")
    llm_ans = record(llm_nodes, "assess_risks")["output"]["evidence"]["answers"]
    llm_risks = llm_state["risks"]
    _, l_qs = Q.risk_questions(Brief(**llm_state["brief"]), [Risk(**{**llm_risks[0], "mitigation": None})])
    raw = {"answers": [to_schema_answer(k, llm_ans[k]) for k in l_qs]}
    norm = {k: normalise(l_qs[k], llm_decider.to_raw(l_qs[k], llm_decider.QuestionAnswer(**raw["answers"][i])))
            .model_dump(exclude_none=True, exclude_defaults=True) for i, k in enumerate(l_qs)}
    rec = record(llm_nodes, "assess_risks")
    put("llm_answers", "The baseline's answers (message.content, the schema's shape) and the same answers normalised",
        pretty({"content": raw, "normalised": norm}),
        note=(f"From the baseline run, whose generative discover_risks step wrote a different risk list; this is its "
              f"first risk, “{llm_risks[0]['title']}”. The whole node: {rec['calls']} calls, {rec['input_tokens']:,} "
              f"input tokens, {rec['output_tokens']:,} output tokens, ${rec['cost_usd']:.4f}, {rec['latency_ms'] / 1000:.1f} s."))

    # --- the census: what every bounded node asks --------------------------------------------------
    sel_caps = [Capability(**c["capability"]) for c in hy_state["capability_selections"] if c["selected"]]
    feats_over = [Feature(**f["feature"]) for f in hy_state["feature_overlaps"] if f["overlaps"]]
    reqs = [Requirement(**r) for r in hy_state["requirements"]]
    outs = [Outcome(**o) for o in hy_state["outcomes"]]
    builders = [
        ("select_capabilities", "capability, channel", lambda: Q.capability_questions(brief, cat.capabilities, cat.channels)),
        ("select_personas", "persona", lambda: Q.persona_questions(brief, sel_caps, cat.personas)),
        ("assess_constraints", "policy, feature", lambda: Q.constraint_questions(brief, sel_caps, cat.policies, cat.features)),
        ("assess_risks", "risk", lambda: Q.risk_questions(brief, risks1)),
        ("classify_requirements", "requirement", lambda: Q.requirement_questions(brief, reqs, feats_over)),
        ("check_coverage", "outcome, goal", lambda: Q.coverage_questions(brief, reqs, outs)),
        ("validate_prd", "check", lambda: Q.validation_questions(hy_state["prd_markdown"], {})),
    ]
    rows = []
    for node, per, build in builders:
        _, qs = build()
        kinds = {}
        for q in qs.values():
            kinds[q["type"]] = kinds.get(q["type"], 0) + 1
        # the state is the final one, so compare with the node's last execution
        recorded = [r for r in hy_nodes if r["node"] == node][-1]["output"]["evidence"]["decisions"]
        rows.append({"node": node, "per": per, "noul": kinds.get("noul", 0), "choice": kinds.get("choice", 0),
                     "score": kinds.get("score", 0), "questions": len(qs), "calls": -(-len(qs) // CHUNK),
                     "recorded": recorded})
    ex["census"] = {"rows": rows, "chunk": CHUNK}
    ex["experiment"] = exp.name
    OUT.write_text(json.dumps(ex, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)} from {exp.name}: {', '.join(k for k in ex if k not in ('experiment',))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
