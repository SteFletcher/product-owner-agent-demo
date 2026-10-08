# Engine selection and assumptions

## How each inference node was classified

The rule applied to every node that needs inference:

1. **Is the answer space enumerable before the call?** If the node picks from a known catalogue,
   assigns a label from a fixed set, places something on a fixed scale, or answers yes/no about a
   given artefact, it is **bounded** and is a Jev candidate.
2. **Does the node have to produce text that did not exist before** (a summary, a list of risks
   nobody enumerated, a requirement, a PRD section)? Then it is **generative** and stays on an LLM
   in both variants. Jev returns probabilities over options, not text, so it cannot do this at all.
3. **Can the answer be computed from the state without a model?** Then it is **deterministic**
   and no engine is called in either variant (priority arithmetic, link coverage, ordering,
   assembly).
4. **Is the answer in an external system?** Then it is **retrieval** over MCP.

The classification is per node, not per workflow: a node that is 80 % computation and 20 %
judgement is split into a deterministic part and a bounded part (`check_coverage`).

## Where Jev was *not* used, and why

| Candidate | Decision | Reason |
|---|---|---|
| Routing after gates (`assess_risks`, `check_coverage`, `validate_prd`) | deterministic | The route is a threshold over probabilities already produced by the bounded node. Asking an engine "should we loop?" would be a second inference on the same evidence. |
| Prioritising requirements | deterministic | Once MoSCoW and the persona/outcome/risk links exist, ordering is arithmetic. |
| Deciding which existing feature a requirement duplicates | bounded, but only as a `noul` per (requirement, feature) pair when the feature list is short | A `choice` across all features per requirement would exceed Jev's option limit for large catalogues; the pairwise form scales but costs more calls. The catalogue here is small, so this stays practical. |
| Scoring PRD quality (benchmark judge) | LLM, blind | The judge is outside the workflow and must stay the same for both variants. Using Jev as the judge of a Jev-assisted workflow would be circular. |

## Why the baseline uses typed questions

The alternative baseline ("ask the LLM to select personas in its own words") would have been
easier to write but would have measured two things at once: the engine *and* the prompt design.
By giving the baseline LLM the same questions, the same state and the same strict answer schema,
the comparison isolates the engine. The baseline is still a fair LLM: it sees everything Jev sees
and can reason over it before answering.

A consequence to keep in mind: an LLM asked for probabilities tends to give coarse values
(0.1, 0.9). The consistency experiment therefore compares *decisions after thresholds*, not raw
probabilities.

## Assumptions and open points

- **Jev via OpenRouter.** `POST https://openrouter.ai/api/v1/systemone`, model
  `typesafe/jev-1.13`. Verified on 2026-10-08: response has `answers`, `usage.{input_tokens,
  output_tokens, cost}`, `id`, `provider`. Output tokens are billed at $0 (confirmed by the
  endpoint pricing: $0.042 / M input, $0 output).
- **Local Jev alternative.** The local Jev-Omni server on `127.0.0.1:8200` speaks the same wire
  (without `usage.cost`). Point `jev.base_url` at it to compare hosted and local Jev; its cost is
  recorded as `unknown` unless a price is configured.
- **No request-level determinism control for Jev.** Jev has no `temperature`/`seed`. Repeated
  identical calls returned identical `noul`/`choice` answers and slightly varying `score`
  expectations in the probe (2.06 / 1.97 / 2.05 / 2.10). The consistency experiment measures this
  rather than assuming it.
- **LLM determinism.** The baseline decision engine runs the LLM at `temperature: 0` where the
  provider honours it, and generative nodes at the configured temperature. Both are recorded in the
  effective config.
- **Context limits.** Jev: 32k tokens state + questions; questions are chunked (default 12 items
  per call) so a large catalogue or risk list never exceeds it. The LLM decision engine uses the
  same chunking so both variants make the same number of calls per node.
- **Answers are isolated.** System One answers questions in parallel and in isolation; one answer
  never conditions another. Nodes are designed so no question depends on another in the same
  batch.
- **Pricing.** OpenRouter reports `usage.cost` per call; the pricing catalogue
  (`config/pricing.yaml`) is used as a cross-check and as the only source for providers that do
  not report cost. If neither exists, cost is `null` and the report says "unknown".
- **Trace propagation.** Neither OpenRouter nor Jev accepts W3C `traceparent`, so engine spans end
  at the client side. The OpenRouter generation id is recorded on the span (`openrouter.id`).
- **Google ADK.** The ADK layer is a custom `BaseAgent` that owns the session and streams the
  graph's progress as ADK events. It intentionally has no model of its own: the Product Owner's
  reasoning lives in the graph, so the ADK layer adds no inference and no cost.
- **MCP server is a mock.** `po_agent/knowledge/server.py` is a real MCP server (stdio) over YAML
  catalogues. Replace it with the enterprise server by changing `knowledge.command` in the config;
  the tool names and shapes are documented in `docs/configuration.md`.
