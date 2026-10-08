"""The typed questions behind every bounded node, and the deterministic policies that turn
answers into decisions. One definition, used by both engines.

Each builder returns (state, questions). The state is what every question is answered against;
per-item questions carry the item inside their instructions, because System One answers each
question in isolation.
"""
from __future__ import annotations

from ..engines.base import choice, noul, score
from ..state import Brief, Capability, Channel, Feature, Outcome, Persona, Policy, Requirement, Risk

IMPACT_LEVELS = ["none: this persona is not affected",
                 "low: a minor change to something they occasionally do",
                 "medium: a noticeable change to a regular task",
                 "high: a core task or outcome for them changes"]
IMPACT_NAMES = ["none", "low", "medium", "high"]

RISK_CATEGORIES = {
    "security": "unauthorised access, fraud, abuse, data breach",
    "privacy": "personal data handled beyond what the task needs, consent, retention",
    "compliance": "a law, regulation or contractual policy could be breached",
    "operational": "day-to-day running: carriers, stores, support load, incidents, SLAs",
    "delivery": "building it: dependencies, change freeze, capacity, integration effort",
    "adoption": "customers or colleagues do not use it, or use it wrongly",
    "financial": "cost, margin, refunds, surcharges, lost revenue",
}
FIVE_LEVELS = ["1 very low", "2 low", "3 moderate", "4 high", "5 very high"]

REQUIREMENT_KINDS = {
    "functional": "behaviour a user or system performs",
    "non_functional": "performance, availability, accessibility, observability, usability quality",
    "data": "what is captured, stored, retained or reported",
    "integration": "an interface with another service, carrier or partner",
    "compliance": "exists to satisfy a policy or regulation",
}
MOSCOW = {
    "must": "without it the initiative does not meet its goals or a policy",
    "should": "important; a workaround exists for a first release",
    "could": "desirable if cheap; no outcome depends on it",
    "wont": "explicitly out of scope for this initiative",
}

PRD_CHECKS = {
    "intent_fidelity": "The PRD describes the same initiative as the brief, with the same goals and scope, and nothing contradicts the brief.",
    "goals_covered": "Every goal in the brief is addressed by at least one requirement in the PRD.",
    "personas_represented": "Every impacted persona in the material appears in the PRD with what changes for them.",
    "requirements_complete": "Every requirement id in the material appears in the PRD with its statement and acceptance criteria.",
    "risks_mitigated": "Every risk in the material appears in the PRD, and every high-priority risk has a mitigation.",
    "constraints_acknowledged": "Every applicable policy in the material is reflected in the PRD's constraints or requirements.",
    "no_unsupported_assumptions": "The PRD does not state facts, numbers or commitments that are not in the brief or the material.",
    "acceptance_criteria_present": "Every Must and Should requirement has testable acceptance criteria.",
    "internally_consistent": "No two parts of the PRD contradict each other (scope, priorities, personas, measures).",
    "actionable": "An engineering team could start delivery from this PRD without needing to ask what is meant.",
}
BLOCKING_CHECKS = {"intent_fidelity", "requirements_complete", "personas_represented", "risks_mitigated",
                   "goals_covered"}


def brief_state(brief: Brief) -> dict:
    return {"title": brief.title, "summary": brief.summary, "goals": brief.goals,
            "in_scope": brief.in_scope, "out_of_scope": brief.out_of_scope}


# --- select_capabilities ------------------------------------------------------------------------


def capability_questions(brief: Brief, capabilities: list[Capability], channels: list[Channel]):
    qs = {}
    for c in capabilities:
        qs[f"cap:{c.id}"] = noul(
            f"Delivering this intent requires changes to, or depends on, the capability "
            f"'{c.name}': {c.description}",
            true="the capability must change or is a direct dependency",
            false="the capability is untouched or only incidentally related")
    for ch in channels:
        qs[f"ch:{ch.id}"] = noul(
            f"The intent changes what customers or colleagues experience in the channel "
            f"'{ch.name}': {ch.description}")
    return brief_state(brief), qs


# --- select_personas ----------------------------------------------------------------------------


def persona_questions(brief: Brief, capabilities: list[Capability], personas: list[Persona]):
    state = {**brief_state(brief), "capabilities_affected": [c.name for c in capabilities]}
    qs = {}
    for p in personas:
        who = f"'{p.name}' ({p.role}): {p.description} Goals: {'; '.join(p.goals)}."
        qs[f"imp:{p.id}"] = noul(f"The persona {who} is affected by this intent, either because "
                                 f"their experience changes or because they operate what changes.")
        qs[f"lvl:{p.id}"] = score(f"How much does this intent change things for the persona {who}",
                                  IMPACT_LEVELS)
    return state, qs


# --- assess_constraints -------------------------------------------------------------------------


def constraint_questions(brief: Brief, capabilities: list[Capability], policies: list[Policy],
                         features: list[Feature]):
    state = {**brief_state(brief), "capabilities_affected": [c.name for c in capabilities]}
    qs = {}
    for p in policies:
        qs[f"pol:{p.id}"] = noul(f"The policy '{p.name}' ({p.kind}): {p.summary} — constrains how this "
                                 f"intent must be delivered.",
                                 true="the initiative must comply with or design around it",
                                 false="it does not bear on this initiative")
    for f in features:
        qs[f"feat:{f.id}"] = noul(f"The existing feature '{f.name}' ({f.status}): {f.description} — "
                                  f"already delivers part of what this intent asks for.")
    return state, qs


# --- assess_risks -------------------------------------------------------------------------------


def risk_questions(brief: Brief, risks: list[Risk]):
    state = brief_state(brief)
    qs = {}
    for r in risks:
        text = f"Risk '{r.title}': {r.description}"
        if r.mitigation:
            text += f" Mitigation in place: {r.mitigation}"
        qs[f"cat:{r.id}"] = choice(f"Which category best fits this risk? {text}", RISK_CATEGORIES)
        qs[f"lik:{r.id}"] = score(f"How likely is this risk to materialise during or after delivery? {text}",
                                  FIVE_LEVELS)
        qs[f"imp:{r.id}"] = score(f"If it materialises, how severe is the impact on customers, the "
                                  f"business or compliance? {text}", FIVE_LEVELS)
        qs[f"inv:{r.id}"] = noul(f"This risk is severe or uncertain enough that the product owner should "
                                 f"investigate it and define a mitigation before writing requirements. {text}")
    return state, qs


def level_value(probabilities: dict[str, float]) -> float:
    """Expected value on a 1..5 scale from probabilities keyed by level index '0'..'4'."""
    return round(sum((int(i) + 1) * p for i, p in probabilities.items()), 3)


# --- classify_requirements ----------------------------------------------------------------------


def requirement_questions(brief: Brief, requirements: list[Requirement], features: list[Feature]):
    state = {**brief_state(brief),
             "existing_features": [f"{f.name}: {f.description}" for f in features]}
    qs = {}
    for r in requirements:
        text = f"Requirement '{r.title}': {r.statement} Rationale: {r.rationale}"
        qs[f"kind:{r.id}"] = choice(f"What kind of requirement is this? {text}", REQUIREMENT_KINDS)
        qs[f"moscow:{r.id}"] = choice(f"How should this requirement be prioritised for the first "
                                      f"release, given the goals? {text}", MOSCOW)
        qs[f"dup:{r.id}"] = noul(f"An existing feature listed in the state already provides what this "
                                 f"requirement asks for, so it is a duplicate rather than new work. {text}")
    return state, qs


MOSCOW_ORDER = {"must": 0, "should": 1, "could": 2, "wont": 3}


def rank_requirements(requirements: list[Requirement]) -> list[Requirement]:
    """Deterministic order: MoSCoW, then outcomes served, then risks mitigated, then id."""
    ordered = sorted(requirements, key=lambda r: (MOSCOW_ORDER.get(r.moscow or "wont", 3),
                                                  -len(r.outcome_ids), -len(r.risk_ids), r.id))
    return [r.model_copy(update={"rank": i + 1}) for i, r in enumerate(ordered)]


# --- check_coverage -----------------------------------------------------------------------------


def coverage_questions(brief: Brief, requirements: list[Requirement], outcomes: list[Outcome]):
    state = {**brief_state(brief),
             "requirements": [f"{r.id} [{r.moscow}] {r.statement}" for r in requirements
                              if r.moscow in ("must", "should")]}
    qs = {}
    for o in outcomes:
        qs[f"del:{o.id}"] = noul(f"The must and should requirements in the state, taken together, "
                                 f"would deliver this outcome: {o.statement} (measured by: {o.measure})")
    for i, g in enumerate(brief.goals):
        qs[f"goal:{i}"] = noul(f"The must and should requirements in the state, taken together, "
                               f"would achieve this goal: {g}")
    return state, qs


# --- validate_prd -------------------------------------------------------------------------------


def validation_questions(prd_markdown: str, material: dict):
    state = {"prd_markdown": prd_markdown, "material": material}
    qs = {k: noul(v) for k, v in PRD_CHECKS.items()}
    return state, qs
