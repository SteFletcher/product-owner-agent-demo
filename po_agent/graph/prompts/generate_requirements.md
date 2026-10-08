Draft the candidate requirements for this initiative.

Write 8 to 16 requirements. Each has:
- id: "req-" followed by a short slug, unique.
- title: under ten words.
- statement: one testable sentence ("The system shall ..." or "A <persona> can ...").
- rationale: why, linking to the outcome or risk it serves.
- persona_ids, outcome_ids, risk_ids: the ids this requirement serves or mitigates (empty lists allowed, but every selected persona and every outcome should be served by at least one requirement).
- acceptance_criteria: two to four Given/When/Then style criteria.

Cover functional behaviour, data, integration with the services listed, compliance with the applicable policies, and non-functional needs (performance, accessibility, observability). Where an existing feature already covers something, build on it rather than duplicating it.

BRIEF:
$brief

IMPACTED PERSONAS:
$personas

OUTCOMES:
$outcomes

RISKS (with priority and mitigation where known):
$risks

APPLICABLE POLICIES:
$policies

EXISTING FEATURES THAT OVERLAP:
$features

SERVICES INVOLVED:
$services
