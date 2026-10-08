Write the Product Requirements Document in Markdown from the structured material below. Everything in the material must appear in the document; do not add requirements, personas or risks that are not in it, and do not drop any.

Use exactly these sections, in this order:

1. `# <title>` then a one-paragraph overview.
2. `## Goals and non-goals`: goals as a list; non-goals from out_of_scope (write "None stated" if empty).
3. `## Impacted personas`: a table with id, persona, impact level, and what changes for them.
4. `## Outcomes and measures`: a table with id, kind, outcome, measure.
5. `## Constraints and policies`: each applicable policy with id and what it means for this initiative.
6. `## Existing capabilities`: the overlapping features and the services involved, and how the initiative builds on them.
7. `## Requirements`: grouped under `### Must`, `### Should`, `### Could`, `### Won't (this time)`. Each requirement as `**<id> <title>**`, the statement, the rationale, linked persona/outcome/risk ids, and acceptance criteria as a list. Keep the given order within each group.
8. `## Risks and mitigations`: a table with id, risk, category, likelihood, impact, priority, mitigation (or "to be defined").
9. `## Assumptions and open questions`: the stated assumptions, plus open questions where the intent was silent.

Be complete but economical: every requirement gets its statement, rationale, links and its acceptance criteria as given (do not expand them); tables stay one line per row; no repetition between sections. Aim for 3,000 to 5,000 words.

Return only the Markdown document, starting with the `#` title line. No preamble, no code fence.

MATERIAL:
$material
