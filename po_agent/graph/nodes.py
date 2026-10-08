"""The nodes. Each is an async function of the state that returns a partial update and the
evidence of what it cost; `Nodes.wrap` adds the span, the metrics and the NodeResult record.

Generative nodes call `ctx.llm`; bounded nodes call `ctx.decider` and never know which engine
is behind it; retrieval nodes call `ctx.knowledge`; deterministic nodes call nothing.
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from pydantic import BaseModel

from .. import telemetry as T
from ..config import Settings
from ..engines.base import DecisionEngine, DecisionResult, LLMEngine, LLMResult
from ..knowledge import Knowledge
from ..state import (
    Brief,
    Capability,
    CapabilitySelection,
    Catalogue,
    Channel,
    ChannelSelection,
    Coverage,
    Feature,
    FeatureOverlap,
    MitigationList,
    NodeResult,
    OutcomeList,
    Persona,
    PersonaSelection,
    Policy,
    PolicyAssessment,
    ProductOwnerState,
    Requirement,
    RequirementList,
    Risk,
    RiskList,
    Service,
    Validation,
    applicable_policies,
    overlapping_features,
    selected_capabilities,
    selected_personas,
)
from . import questions as Q

PROMPTS = Path(__file__).parent / "prompts"
GENERATIVE, BOUNDED, RETRIEVAL, DETERMINISTIC = "generative", "bounded", "retrieval", "deterministic"


def prompt(name: str, **values: Any) -> str:
    return Template((PROMPTS / f"{name}.md").read_text()).substitute(
        {k: (v if isinstance(v, str) else dumps(v)) for k, v in values.items()})


SYSTEM = (PROMPTS / "system.md").read_text()


def dumps(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, indent=1, sort_keys=True)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class Evidence:
    engine: str = "none"
    model: str | None = None
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = 0.0
    cost_source: str = "catalogue"
    calls: int = 0
    retries: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, r: LLMResult | DecisionResult, engine: str) -> Evidence:
        return cls(engine=engine, model=r.model, latency_ms=r.latency_ms, input_tokens=r.usage.input_tokens,
                   output_tokens=r.usage.output_tokens, cost=r.usage.cost.value,
                   cost_source=r.usage.cost.source, calls=getattr(r, "calls", 1), retries=r.retries)

    def merge(self, other: Evidence) -> Evidence:
        cost = None if self.cost is None or other.cost is None else self.cost + other.cost
        return Evidence(engine=other.engine if self.engine == "none" else self.engine,
                        model=self.model or other.model, latency_ms=self.latency_ms + other.latency_ms,
                        input_tokens=self.input_tokens + other.input_tokens,
                        output_tokens=self.output_tokens + other.output_tokens, cost=cost,
                        cost_source=self.cost_source if self.cost_source == other.cost_source else "mixed",
                        calls=self.calls + other.calls, retries=self.retries + other.retries,
                        extra={**self.extra, **other.extra})


NodeFn = Callable[[ProductOwnerState], Awaitable[tuple[dict, Evidence]]]


@dataclass
class RunContext:
    settings: Settings
    llm: LLMEngine
    decider: DecisionEngine
    knowledge: Knowledge
    telemetry: T.Telemetry
    variant: str
    experiment_id: str
    run_id: str


@dataclass
class NodeSpec:
    name: str
    node_type: str
    fn: NodeFn
    inputs: tuple[str, ...]          # state keys hashed as the node's input
    attempt_key: str | None = None   # state counter this node increments


class Nodes:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.th = ctx.settings.thresholds
        self.specs: dict[str, NodeSpec] = {}
        for spec in [
            NodeSpec("parse_intent", GENERATIVE, self.parse_intent, ("intent_text",)),
            NodeSpec("retrieve_knowledge", RETRIEVAL, self.retrieve_knowledge, ("brief",)),
            NodeSpec("select_capabilities", BOUNDED, self.select_capabilities, ("brief", "catalogue")),
            NodeSpec("select_personas", BOUNDED, self.select_personas, ("brief", "capability_selections", "catalogue")),
            NodeSpec("identify_outcomes", GENERATIVE, self.identify_outcomes, ("brief", "persona_selections")),
            NodeSpec("retrieve_constraints", RETRIEVAL, self.retrieve_constraints, ("capability_selections",)),
            NodeSpec("assess_constraints", BOUNDED, self.assess_constraints, ("brief", "capability_selections", "catalogue")),
            NodeSpec("discover_risks", GENERATIVE, self.discover_risks, ("brief", "persona_selections", "policy_assessments", "catalogue")),
            NodeSpec("assess_risks", BOUNDED, self.assess_risks, ("brief", "risks"), "risk_attempt"),
            NodeSpec("investigate_risks", GENERATIVE, self.investigate_risks, ("brief", "risks", "catalogue")),
            NodeSpec("generate_requirements", GENERATIVE, self.generate_requirements,
                     ("brief", "persona_selections", "outcomes", "risks", "policy_assessments", "feature_overlaps", "catalogue")),
            NodeSpec("classify_requirements", BOUNDED, self.classify_requirements, ("brief", "requirements", "feature_overlaps"), "requirement_attempt"),
            NodeSpec("check_coverage", BOUNDED, self.check_coverage, ("brief", "requirements", "persona_selections", "outcomes")),
            NodeSpec("refine_requirements", GENERATIVE, self.refine_requirements, ("coverage", "requirements", "brief", "persona_selections", "outcomes")),
            NodeSpec("construct_prd", GENERATIVE, self.construct_prd, ("brief", "persona_selections", "outcomes", "requirements", "risks", "policy_assessments", "feature_overlaps", "catalogue")),
            NodeSpec("validate_prd", BOUNDED, self.validate_prd, ("prd_markdown", "brief", "requirements", "persona_selections", "risks"), "prd_attempt"),
            NodeSpec("refine_prd", GENERATIVE, self.refine_prd, ("prd_markdown", "validation")),
            NodeSpec("finalise", DETERMINISTIC, self.finalise, ("prd_markdown",)),
        ]:
            self.specs[spec.name] = spec

    # --- the wrapper --------------------------------------------------------------------------

    def wrap(self, spec: NodeSpec) -> Callable[[ProductOwnerState], Awaitable[dict]]:
        ctx = self.ctx

        async def run(state: ProductOwnerState) -> dict:
            attempt = (state.get(spec.attempt_key, 0) + 1) if spec.attempt_key else 1
            engine_type = {GENERATIVE: "llm", BOUNDED: ctx.decider.engine_type, RETRIEVAL: "mcp",
                           DETERMINISTIC: "none"}[spec.node_type]
            attrs = {T.EXPERIMENT_ID: ctx.experiment_id, T.RUN_ID: ctx.run_id, T.VARIANT: ctx.variant,
                     T.NODE_NAME: spec.name, T.NODE_TYPE: spec.node_type, T.NODE_ATTEMPT: attempt,
                     T.ENGINE_TYPE: engine_type}
            input_hash = digest({k: state.get(k) for k in spec.inputs})
            started = time.perf_counter()
            with ctx.telemetry.span(spec.name, attrs) as span:
                ids = T.current_trace_ids()
                try:
                    update, ev = await spec.fn(state)
                except Exception as e:
                    ms = (time.perf_counter() - started) * 1000
                    ctx.telemetry.record_node(attrs, ms, False)
                    record = NodeResult(node=spec.name, node_type=spec.node_type, attempt=attempt,
                                        engine=engine_type, model=None, latency_ms=ms, input_tokens=0,
                                        output_tokens=0, cost_usd=None, cost_source="unknown", calls=0,
                                        retries=0, status="error", input_hash=input_hash, output_hash="",
                                        output=None, error=f"{type(e).__name__}: {e}", **ids)
                    raise NodeFailed(record) from e
                ms = (time.perf_counter() - started) * 1000
                if spec.attempt_key:
                    update[spec.attempt_key] = attempt
                span.set_attributes({T.LATENCY_MS: round(ms, 1), T.INPUT_TOKENS: ev.input_tokens,
                                     T.OUTPUT_TOKENS: ev.output_tokens, T.RETRY_COUNT: ev.retries,
                                     T.COST_SOURCE: ev.cost_source, "calls": ev.calls,
                                     **{f"node.{k}": v for k, v in ev.extra.items()
                                        if isinstance(v, (str, int, float, bool))}})
                if ev.model:
                    span.set_attribute(T.MODEL_NAME, ev.model)
                if ev.cost is not None:
                    span.set_attribute(T.TOTAL_COST, ev.cost)
                ctx.telemetry.record_node({**attrs, T.MODEL_NAME: ev.model}, ms, True,
                                          ev.input_tokens, ev.output_tokens, ev.cost)
                output = {k: v for k, v in update.items() if k not in ("node_results", "errors")}
                record = NodeResult(node=spec.name, node_type=spec.node_type, attempt=attempt,
                                    engine=ev.engine if ev.engine != "none" else engine_type,
                                    model=ev.model, latency_ms=ms, input_tokens=ev.input_tokens,
                                    output_tokens=ev.output_tokens, cost_usd=ev.cost,
                                    cost_source=ev.cost_source, calls=ev.calls, retries=ev.retries,
                                    status="ok", input_hash=input_hash, output_hash=digest(output),
                                    output={**_plain(output), **({"evidence": ev.extra} if ev.extra else {})},
                                    **ids)
                return {**update, "node_results": [record]}
        return run

    # --- helpers ------------------------------------------------------------------------------

    async def _gen(self, node: str, schema: type[BaseModel], **values: Any) -> tuple[BaseModel, Evidence]:
        r = await self.ctx.llm.generate(system=SYSTEM, prompt=prompt(node, **values), schema=schema,
                                        purpose=node, model=self.ctx.settings.llm.model_for(node))
        return r.data, Evidence.of(r, "llm")

    async def _gen_markdown(self, node: str, **values: Any) -> tuple[str, Evidence]:
        """Long documents come back as plain Markdown: no JSON escaping, no schema, bigger budget."""
        r = await self.ctx.llm.generate(system=SYSTEM, prompt=prompt(node, **values), schema=None,
                                        purpose=node, model=self.ctx.settings.llm.model_for(node),
                                        max_tokens=self.ctx.settings.llm.prd_max_tokens)
        text = r.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return text, Evidence.of(r, "llm")

    async def _decide(self, node: str, state: Any, questions: dict) -> tuple[DecisionResult, Evidence]:
        r = await self.ctx.decider.decide(state=state, questions=questions, purpose=node)
        ev = Evidence.of(r, self.ctx.decider.engine_type)
        confs = [a.confidence for a in r.answers.values()]
        ev.extra = {"decisions": len(r.answers),
                    "mean_confidence": round(sum(confs) / len(confs), 4) if confs else None,
                    "answers": {k: a.model_dump(exclude_none=True) for k, a in r.answers.items()}}
        return r, ev

    def _material(self, state: ProductOwnerState) -> dict:
        cat: Catalogue = state["catalogue"]
        return {
            "brief": state["brief"],
            "personas": [{"id": s.persona.id, "name": s.persona.name, "role": s.persona.role,
                          "impact_level": s.impact_level, "description": s.persona.description}
                         for s in selected_personas(state)],
            "outcomes": state.get("outcomes", []),
            "policies": applicable_policies(state),
            "existing_features": overlapping_features(state),
            "services": [s for s in cat.services if set(s.capability_ids) & {c.id for c in selected_capabilities(state)}],
            "requirements": state.get("requirements", []),
            "risks": state.get("risks", []),
        }

    # --- generative ---------------------------------------------------------------------------

    async def parse_intent(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        brief, ev = await self._gen("parse_intent", Brief, intent=state["intent_text"])
        return {"brief": brief}, ev

    async def identify_outcomes(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        personas = [{"id": s.persona.id, "name": s.persona.name, "role": s.persona.role,
                     "impact": s.impact_level} for s in selected_personas(state)]
        out, ev = await self._gen("identify_outcomes", OutcomeList, brief=state["brief"], personas=personas)
        return {"outcomes": out.outcomes}, ev

    async def discover_risks(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        m = self._material(state)
        out, ev = await self._gen("discover_risks", RiskList, brief=state["brief"], personas=m["personas"],
                                  policies=m["policies"], services=m["services"])
        risks = [Risk(**d.model_dump()) for d in out.risks]
        return {"risks": risks, "risk_attempt": 0}, ev

    async def investigate_risks(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        flagged = [r for r in state["risks"] if r.needs_investigation]
        m = self._material(state)
        out, ev = await self._gen("investigate_risks", MitigationList, brief=state["brief"],
                                  services=m["services"], risks=flagged)
        by_id = {x.risk_id: x for x in out.mitigations}
        risks = []
        for r in state["risks"]:
            if r.id in by_id:
                x = by_id[r.id]
                r = r.model_copy(update={"description": x.revised_description, "mitigation": x.mitigation,
                                         "residual_concern": x.residual_concern})
            risks.append(r)
        ev.extra = {"investigated": len(by_id)}
        return {"risks": risks}, ev

    async def generate_requirements(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        m = self._material(state)
        out, ev = await self._gen("generate_requirements", RequirementList, brief=state["brief"],
                                  personas=m["personas"], outcomes=m["outcomes"], risks=m["risks"],
                                  policies=m["policies"], features=m["existing_features"], services=m["services"])
        reqs = [Requirement(**d.model_dump()) for d in out.requirements]
        return {"requirements": reqs, "requirement_attempt": 0}, ev

    async def refine_requirements(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        m = self._material(state)
        out, ev = await self._gen("refine_requirements", RequirementList, gaps=state["coverage"].gaps,
                                  requirements=state["requirements"], brief=state["brief"],
                                  personas=m["personas"], outcomes=m["outcomes"])
        merged = {r.id: r for r in state["requirements"]}
        for d in out.requirements:
            merged[d.id] = Requirement(**d.model_dump())
        ev.extra = {"changed": len(out.requirements)}
        return {"requirements": list(merged.values())}, ev

    async def construct_prd(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        text, ev = await self._gen_markdown("construct_prd", material=self._material(state))
        return {"prd_markdown": text, "prd_attempt": 0}, ev

    async def refine_prd(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        v: Validation = state["validation"]
        failures = "\n".join(f"- {k}: {Q.PRD_CHECKS[k]} (P(pass) = {v.checks[k]:.2f})" for k in v.failed)
        text, ev = await self._gen_markdown("refine_prd", failures=failures, prd=state["prd_markdown"],
                                            material=self._material(state))
        return {"prd_markdown": text}, ev

    # --- retrieval ----------------------------------------------------------------------------

    async def retrieve_knowledge(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        k = self.ctx.knowledge
        caps, chans, pers = (await k.call("get_capabilities"), await k.call("get_channels"),
                             await k.call("get_personas"))
        cat = Catalogue(capabilities=[Capability(**c) for c in caps], channels=[Channel(**c) for c in chans],
                        personas=[Persona(**p) for p in pers])
        return {"catalogue": cat}, Evidence(engine="mcp", calls=3,
                                            extra={"capabilities": len(caps), "channels": len(chans),
                                                   "personas": len(pers)})

    async def retrieve_constraints(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        k = self.ctx.knowledge
        ids = [c.id for c in selected_capabilities(state)]
        pols = await k.call("search_policies", capability_ids=ids)
        feats = await k.call("get_existing_features", capability_ids=ids)
        svcs = await k.call("get_services", capability_ids=ids)
        cat = state["catalogue"].model_copy(update={
            "policies": [Policy(**p) for p in pols], "features": [Feature(**f) for f in feats],
            "services": [Service(**s) for s in svcs]})
        return {"catalogue": cat}, Evidence(engine="mcp", calls=3,
                                            extra={"policies": len(pols), "features": len(feats),
                                                   "services": len(svcs)})

    # --- bounded ------------------------------------------------------------------------------

    async def select_capabilities(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        cat = state["catalogue"]
        s, qs = Q.capability_questions(state["brief"], cat.capabilities, cat.channels)
        r, ev = await self._decide("select_capabilities", s, qs)
        caps = [CapabilitySelection(capability=c, p_affected=r.answers[f"cap:{c.id}"].noul,
                                    selected=r.answers[f"cap:{c.id}"].noul >= self.th.capability_selected)
                for c in cat.capabilities]
        chans = [ChannelSelection(channel=c, p_affected=r.answers[f"ch:{c.id}"].noul,
                                  selected=r.answers[f"ch:{c.id}"].noul >= self.th.capability_selected)
                 for c in cat.channels]
        ev.extra.update(selected_capabilities=sum(c.selected for c in caps),
                        selected_channels=sum(c.selected for c in chans))
        return {"capability_selections": caps, "channel_selections": chans}, ev

    async def select_personas(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        cat = state["catalogue"]
        s, qs = Q.persona_questions(state["brief"], selected_capabilities(state), cat.personas)
        r, ev = await self._decide("select_personas", s, qs)
        sels = []
        for p in cat.personas:
            imp, lvl = r.answers[f"imp:{p.id}"], r.answers[f"lvl:{p.id}"]
            level = Q.IMPACT_NAMES[int(max(lvl.probabilities, key=lvl.probabilities.get))]
            sels.append(PersonaSelection(persona=p, p_impacted=imp.noul, impact=lvl.score,
                                         impact_level=level, confidence=min(imp.confidence, lvl.confidence),
                                         selected=imp.noul >= self.th.persona_selected and level != "none"))
        ev.extra.update(selected_personas=sum(x.selected for x in sels))
        return {"persona_selections": sels}, ev

    async def assess_constraints(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        cat = state["catalogue"]
        s, qs = Q.constraint_questions(state["brief"], selected_capabilities(state), cat.policies, cat.features)
        r, ev = await self._decide("assess_constraints", s, qs)
        pols = [PolicyAssessment(policy=p, p_applies=r.answers[f"pol:{p.id}"].noul,
                                 applies=r.answers[f"pol:{p.id}"].noul >= self.th.policy_applies)
                for p in cat.policies]
        feats = [FeatureOverlap(feature=f, p_overlaps=r.answers[f"feat:{f.id}"].noul,
                                overlaps=r.answers[f"feat:{f.id}"].noul >= self.th.feature_overlaps)
                 for f in cat.features]
        ev.extra.update(applicable_policies=sum(p.applies for p in pols),
                        overlapping_features=sum(f.overlaps for f in feats))
        return {"policy_assessments": pols, "feature_overlaps": feats}, ev

    async def assess_risks(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        attempt = state.get("risk_attempt", 0) + 1
        s, qs = Q.risk_questions(state["brief"], state["risks"])
        r, ev = await self._decide("assess_risks", s, qs)
        risks = []
        for risk in state["risks"]:
            cat, lik, imp, inv = (r.answers[f"{k}:{risk.id}"] for k in ("cat", "lik", "imp", "inv"))
            likelihood, impact = Q.level_value(lik.probabilities), Q.level_value(imp.probabilities)
            priority = round(likelihood * impact, 2)
            # Already-investigated risks are not sent round again; the cap in the graph is the backstop.
            needs = (inv.noul >= self.th.risk_investigate or priority >= self.th.risk_priority_investigate) \
                and risk.mitigation is None
            risks.append(risk.model_copy(update={
                "category": cat.choice, "category_probabilities": cat.probabilities,
                "likelihood": likelihood, "impact": impact, "priority": priority,
                "p_needs_investigation": inv.noul, "needs_investigation": needs, "assessed_attempt": attempt}))
        risks.sort(key=lambda x: (-(x.priority or 0), x.id))
        ev.extra.update(flagged=sum(x.needs_investigation for x in risks))
        return {"risks": risks}, ev

    async def classify_requirements(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        s, qs = Q.requirement_questions(state["brief"], state["requirements"], overlapping_features(state))
        r, ev = await self._decide("classify_requirements", s, qs)
        reqs = []
        for req in state["requirements"]:
            kind, moscow, dup = (r.answers[f"{k}:{req.id}"] for k in ("kind", "moscow", "dup"))
            reqs.append(req.model_copy(update={
                "kind": kind.choice, "kind_probabilities": kind.probabilities,
                "moscow": moscow.choice, "moscow_probabilities": moscow.probabilities,
                "p_duplicates_existing": dup.noul, "duplicates_existing": dup.noul >= self.th.feature_overlaps}))
        reqs = Q.rank_requirements(reqs)
        ev.extra.update(must=sum(x.moscow == "must" for x in reqs), duplicates=sum(x.duplicates_existing for x in reqs))
        return {"requirements": reqs}, ev

    async def check_coverage(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        reqs = [r for r in state["requirements"] if r.moscow in ("must", "should") and not r.duplicates_existing]
        linked_personas = {p for r in reqs for p in r.persona_ids}
        linked_outcomes = {o for r in reqs for o in r.outcome_ids}
        uncovered_p = [s.persona.id for s in selected_personas(state) if s.persona.id not in linked_personas]
        uncovered_o = [o.id for o in state["outcomes"] if o.id not in linked_outcomes]
        s, qs = Q.coverage_questions(state["brief"], state["requirements"], state["outcomes"])
        r, ev = await self._decide("check_coverage", s, qs)
        delivery = {o.id: r.answers[f"del:{o.id}"].noul for o in state["outcomes"]}
        undelivered = [oid for oid, p in delivery.items() if p < self.th.outcome_delivered]
        goals = state["brief"].goals
        unmet_goals = [g for i, g in enumerate(goals) if r.answers[f"goal:{i}"].noul < self.th.outcome_delivered]
        names = {s.persona.id: s.persona.name for s in state["persona_selections"]}
        outs = {o.id: o.statement for o in state["outcomes"]}
        gaps = ([f"No must/should requirement serves persona {p} ({names.get(p)})" for p in uncovered_p]
                + [f"No must/should requirement is linked to outcome {o}: {outs.get(o)}" for o in uncovered_o]
                + [f"Requirements would not deliver outcome {o} (P={delivery[o]:.2f}): {outs.get(o)}" for o in undelivered]
                + [f"Requirements would not achieve goal: {g}" for g in unmet_goals])
        cov = Coverage(uncovered_persona_ids=uncovered_p, uncovered_outcome_ids=uncovered_o,
                       uncovered_goals=unmet_goals, outcome_delivery=delivery, undelivered_outcome_ids=undelivered,
                       sufficient=not gaps, gaps=gaps)
        ev.extra.update(gaps=len(gaps), sufficient=cov.sufficient)
        return {"coverage": cov}, ev

    async def validate_prd(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        s, qs = Q.validation_questions(state["prd_markdown"], _plain(self._material(state)))
        r, ev = await self._decide("validate_prd", s, qs)
        checks = {k: r.answers[k].noul for k in Q.PRD_CHECKS}
        failed = [k for k, p in checks.items() if p < self.th.validation_pass]
        passed = not any(k in Q.BLOCKING_CHECKS for k in failed)
        ev.extra.update(failed=len(failed), passed=passed)
        return {"validation": Validation(checks=checks, failed=failed, passed=passed)}, ev

    # --- deterministic ------------------------------------------------------------------------

    async def finalise(self, state: ProductOwnerState) -> tuple[dict, Evidence]:
        v = state.get("validation")
        header = "\n".join([
            "---", f"variant: {self.ctx.variant}", f"experiment: {self.ctx.experiment_id}",
            f"run: {self.ctx.run_id}", f"llm_model: {self.ctx.settings.llm.model}",
            f"decision_engine: {self.ctx.decider.engine_type} ({self.ctx.decider.model})",
            f"validation_passed: {v.passed if v else 'n/a'}", f"prd_attempts: {state.get('prd_attempt', 1)}",
            "---", ""])
        return {"prd_final": header + state["prd_markdown"]}, Evidence()


class NodeFailed(Exception):
    def __init__(self, record: NodeResult):
        super().__init__(record.error)
        self.record = record
