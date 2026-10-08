# Configuration

Everything lives in `config/config.yaml` and `config/pricing.yaml`. Secrets come from the
environment (`.env`, loaded by the Makefile). Any string value may contain `${VAR}` or
`${VAR:-default}`. The effective configuration (resolved, secrets replaced by `api_key_set`) is
written into every experiment directory.

Override the file locations with `PO_CONFIG` and `PO_PRICING`, or `--config` / `--pricing`.

## `llm`

| Key | Meaning |
|---|---|
| `provider` | Label recorded in telemetry (`engine.provider`). |
| `base_url` | OpenAI-compatible chat completions base. OpenRouter: `https://openrouter.ai/api/v1`. LM Studio: `http://127.0.0.1:1234/v1`. |
| `api_key_env` | Environment variable holding the key. Empty for servers that need none. |
| `model` | Model for generative nodes in both variants. |
| `decision_model` | Model the **baseline** uses for bounded nodes (defaults to `model`). |
| `node_models` | Per-node overrides, e.g. `construct_prd: anthropic/claude-sonnet-5`. |
| `temperature`, `decision_temperature` | Generative and bounded temperatures. |
| `max_tokens`, `timeout_s`, `retries` | Per call. Retries apply to transport errors and schema-invalid output. |

Structured output uses `response_format: json_schema` with `strict: true`; on OpenRouter,
`provider.require_parameters` restricts routing to providers that honour it.

## `jev`

| Key | Meaning |
|---|---|
| `base_url` + `endpoint` | `https://openrouter.ai/api` + `/v1/systemone` (hosted), or `http://127.0.0.1:8200` + `/v1/systemone` (local Jev-Omni). |
| `api_key_env` | `OPENROUTER_API_KEY` for hosted; empty for local. |
| `model` | `typesafe/jev-1.13`, `~typesafe/jev-latest`, or `jev-omni` locally. |
| `chunk_size` | Per-item questions per call (both engines use it, so call counts match). |

## `judge`

The blind PRD judge. Keep it fixed across experiments you want to compare, and different from
the generative model where possible.

## `knowledge`

`transport: stdio` runs `command` as an MCP server (default: the bundled catalogue server).
`transport: inprocess` calls the same tools without a subprocess (tests, offline). To use a real
enterprise server, point `command` at it; it must expose these tools:

| Tool | Arguments | Returns |
|---|---|---|
| `get_capabilities` | – | `[{id, name, description, owner}]` |
| `get_channels` | – | `[{id, name, description}]` |
| `get_personas` | – | `[{id, name, role, description, goals[], pain_points[]}]` |
| `search_policies` | `capability_ids[]` | `[{id, name, summary, kind, capability_ids[]}]` |
| `get_existing_features` | `capability_ids[]` | `[{id, name, description, status, capability_ids[]}]` |
| `get_services` | `capability_ids[]` | `[{id, name, description, owner, capability_ids[]}]` |
| `get_glossary` | – | `[{term, definition}]` |

## `telemetry`

`otlp_endpoint` is the collector's OTLP/HTTP base (traces go to `/v1/traces`, metrics to
`/v1/metrics`). `PO_TELEMETRY=0` disables export entirely (tests). `service_name` is the
Tempo/Prometheus `service.name`.

## `thresholds` and `loops`

The deterministic policies applied to probabilities, and the loop caps. They are part of the
experiment: the same values apply to both variants and are recorded in `config.yaml` of every
result. Lower `validation_pass` to make PRD refinement rarer; raise `risk_investigate` to
investigate fewer risks.

## `benchmark`

| Key | Meaning |
|---|---|
| `results_dir` | Root for `results/<experiment-id>/`. |
| `runs` | Default `RUNS`. |
| `consistency_repeats` | Replays per bounded node per engine (`0` skips). |
| `judge_both_orders` | Judge each pair twice (A/B and B/A) to cancel position bias; doubles judge cost. |

## Pricing

`config/pricing.yaml` lists USD per million tokens per model, with an `as_of` date. OpenRouter
reports `usage.cost` per call, which takes precedence (`cost.source: reported`). A model with
neither has `cost.source: unknown` and the report prints `unknown` instead of a number.

## Swapping engines between runs

```bash
# hosted Jev vs local Jev-Omni
sed -i '' 's#base_url: https://openrouter.ai/api$#base_url: http://127.0.0.1:8200#' config/config.yaml
# or keep two config files and pick one
make benchmark PO_CONFIG=config/config.local-jev.yaml
```
