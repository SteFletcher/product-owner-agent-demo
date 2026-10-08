"""The graph state: Pydantic models for every structured value, a TypedDict for the whole.

Lists are replaced by the node that owns them. `node_results` and `errors` accumulate across the
run (operator.add), so a loop's second attempt sits next to its first in the record.

Every model that an LLM must produce is strict: no defaults on the fields the model fills in, so
`strict_schema()` can mark them all required, as OpenRouter's structured-output mode requires.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

# --- what the LLM produces -----------------------------------------------------------------------


class Brief(BaseModel):
    title: str
    summary: str
    goals: list[str]
    in_scope: list[str]
    out_of_scope: list[str]
    actors_mentioned: list[str]
    stated_assumptions: list[str]
    success_signals: list[str]


class Outcome(BaseModel):
    id: str
    kind: Literal["user", "business"]
    statement: str
    measure: str
    persona_ids: list[str]


class RiskDraft(BaseModel):
    id: str
    title: str
    description: str
    affected_persona_ids: list[str]


class RiskList(BaseModel):
    risks: list[RiskDraft]


class OutcomeList(BaseModel):
    outcomes: list[Outcome]


class Mitigation(BaseModel):
    risk_id: str
    revised_description: str
    mitigation: str
    residual_concern: str


class MitigationList(BaseModel):
    mitigations: list[Mitigation]


class RequirementDraft(BaseModel):
    id: str
    title: str
    statement: str
    rationale: str
    persona_ids: list[str]
    outcome_ids: list[str]
    risk_ids: list[str]
    acceptance_criteria: list[str]


class RequirementList(BaseModel):
    requirements: list[RequirementDraft]


class PRDText(BaseModel):
    markdown: str


# --- catalogue entries, as the MCP server returns them ------------------------------------------


class Capability(BaseModel):
    id: str
    name: str
    description: str
    owner: str


class Channel(BaseModel):
    id: str
    name: str
    description: str


class Persona(BaseModel):
    id: str
    name: str
    role: str
    description: str
    goals: list[str]
    pain_points: list[str]


class Policy(BaseModel):
    id: str
    name: str
    summary: str
    capability_ids: list[str]
    kind: Literal["regulatory", "security", "privacy", "operational", "commercial", "accessibility"]


class Feature(BaseModel):
    id: str
    name: str
    description: str
    capability_ids: list[str]
    status: str


class Service(BaseModel):
    id: str
    name: str
    description: str
    capability_ids: list[str]
    owner: str


# --- decisions, with their evidence -------------------------------------------------------------


class Answer(BaseModel):
    """One System One answer, normalised to the same shape whichever engine produced it."""
    type: Literal["noul", "choice", "score"]
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    probabilities: dict[str, float] = Field(default_factory=dict)
    legend: dict[str, str] = Field(default_factory=dict)
    confidence: float = 1.0


class CapabilitySelection(BaseModel):
    capability: Capability
    p_affected: float
    selected: bool


class ChannelSelection(BaseModel):
    channel: Channel
    p_affected: float
    selected: bool


class PersonaSelection(BaseModel):
    persona: Persona
    p_impacted: float
    impact: float               # expected level on 0..3
    impact_level: str           # none | low | medium | high (most likely level)
    confidence: float
    selected: bool


class PolicyAssessment(BaseModel):
    policy: Policy
    p_applies: float
    applies: bool


class FeatureOverlap(BaseModel):
    feature: Feature
    p_overlaps: float
    overlaps: bool


RiskCategory = Literal["security", "privacy", "compliance", "operational", "delivery",
                       "adoption", "financial"]


class Risk(BaseModel):
    id: str
    title: str
    description: str
    affected_persona_ids: list[str]
    category: RiskCategory | None = None
    category_probabilities: dict[str, float] = Field(default_factory=dict)
    likelihood: float | None = None      # expected value on 1..5
    impact: float | None = None
    priority: float | None = None        # likelihood x impact
    p_needs_investigation: float | None = None
    needs_investigation: bool = False
    mitigation: str | None = None
    residual_concern: str | None = None
    assessed_attempt: int = 0


RequirementKind = Literal["functional", "non_functional", "data", "integration", "compliance"]
Moscow = Literal["must", "should", "could", "wont"]


class Requirement(BaseModel):
    id: str
    title: str
    statement: str
    rationale: str
    persona_ids: list[str]
    outcome_ids: list[str]
    risk_ids: list[str]
    acceptance_criteria: list[str]
    kind: RequirementKind | None = None
    kind_probabilities: dict[str, float] = Field(default_factory=dict)
    moscow: Moscow | None = None
    moscow_probabilities: dict[str, float] = Field(default_factory=dict)
    p_duplicates_existing: float | None = None
    duplicates_existing: bool = False
    rank: int | None = None


class Coverage(BaseModel):
    uncovered_persona_ids: list[str]
    uncovered_outcome_ids: list[str]
    uncovered_goals: list[str]
    outcome_delivery: dict[str, float]   # outcome id -> P(requirements deliver it)
    undelivered_outcome_ids: list[str]
    sufficient: bool
    gaps: list[str]


class Validation(BaseModel):
    checks: dict[str, float]             # check id -> P(passes)
    failed: list[str]
    passed: bool


# --- the record of what each node did ------------------------------------------------------------


class NodeResult(BaseModel):
    node: str
    node_type: str
    attempt: int
    engine: str                          # llm | jev | mcp | none
    model: str | None
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cost_source: str
    calls: int
    retries: int
    status: str
    input_hash: str
    output_hash: str
    output: Any
    trace_id: str | None = None
    span_id: str | None = None
    error: str | None = None


class Catalogue(BaseModel):
    capabilities: list[Capability] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    personas: list[Persona] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    services: list[Service] = Field(default_factory=list)


class ProductOwnerState(TypedDict, total=False):
    intent_text: str
    brief: Brief
    catalogue: Catalogue
    capability_selections: list[CapabilitySelection]
    channel_selections: list[ChannelSelection]
    persona_selections: list[PersonaSelection]
    outcomes: list[Outcome]
    policy_assessments: list[PolicyAssessment]
    feature_overlaps: list[FeatureOverlap]
    risks: list[Risk]
    risk_attempt: int
    requirements: list[Requirement]
    requirement_attempt: int
    coverage: Coverage
    prd_markdown: str
    prd_attempt: int
    validation: Validation
    prd_final: str
    node_results: Annotated[list[NodeResult], operator.add]
    errors: Annotated[list[str], operator.add]


def selected_personas(state: ProductOwnerState) -> list[PersonaSelection]:
    return [s for s in state.get("persona_selections", []) if s.selected]


def selected_capabilities(state: ProductOwnerState) -> list[Capability]:
    return [s.capability for s in state.get("capability_selections", []) if s.selected]


def applicable_policies(state: ProductOwnerState) -> list[Policy]:
    return [a.policy for a in state.get("policy_assessments", []) if a.applies]


def overlapping_features(state: ProductOwnerState) -> list[Feature]:
    return [o.feature for o in state.get("feature_overlaps", []) if o.overlaps]
