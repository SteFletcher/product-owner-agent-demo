# Product Owner agent: LLM vs LLM + Jev

One agentic Product Owner workflow (intent document → PRD), run two ways on the same graph:

- **llm**: an LLM makes every decision.
- **hybrid**: an LLM writes; **Jev** (TypeSafe's System One decision model) makes the bounded decisions: which personas, which policies apply, risk scoring, MoSCoW, coverage, validation.

The benchmark measures latency, tokens, cost, consistency and blind-judged PRD quality per node and per run, with every run traced in OpenTelemetry.

Docs: **https://stefletcher.github.io/product-owner-agent-demo/** (executive summary, architecture, graph, engine rationale, configuration).

## Run it in five minutes

Needs: Python 3.12 (via [uv](https://docs.astral.sh/uv/)), an [OpenRouter](https://openrouter.ai) key (it serves both the LLMs and Jev), and optionally a local OTel collector + Grafana for traces and dashboards.

```bash
make setup                      # .venv, dependencies, .env from the example
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
make check                      # key, collector, MCP server, Jev, LLM — all should say [ok]

make run-hybrid                 # one run, ~2 min, a few cents
make run-llm
make benchmark RUNS=3           # both variants x3, blind judge, consistency replay, report
```

Results land in `results/<experiment-id>/` with the two PRDs, per-node metrics, trace ids, raw judgements and `comparison.md`.

Change the models in `config/config.yaml` (no code changes); prices in `config/pricing.yaml`. Without an OTel collector, set `PO_TELEMETRY=0` or `telemetry.enabled: false`; the runs still work.

## Grafana

With the local [otel-stack](https://github.com/SteFletcher) running (collector on 4318, Grafana on 3001):

```bash
make dashboard                  # generates grafana/po-benchmark.json and pushes it
```

The board shows the workflow node by node for both variants, with latency, tokens and cost, and links each node to its Tempo traces.

## Develop

```bash
make test                       # offline: unit + graph routing with fake engines
make lint
make graph                      # Mermaid of the compiled graph
```

Layout: `po_agent/graph` (nodes, questions, prompts), `po_agent/engines` (OpenRouter LLM, Jev, LLM-as-decider), `po_agent/knowledge` (MCP server + client over `data/catalogue.yaml`), `po_agent/evaluation` (judge, consistency), `po_agent/benchmark.py`, `po_agent/adk_app.py` (Google ADK agent).
