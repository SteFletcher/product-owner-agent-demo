# Entry points. `make help` lists them. Secrets come from .env (see .env.example).
.DEFAULT_GOAL := help
-include .env
export
export PYTHONUNBUFFERED = 1     # progress lines reach a redirected log as they happen

PY      := .venv/bin/python
INTENT  ?= examples/example-intent.md
RUNS    ?= 1
REPEATS ?=                       # consistency repeats per bounded node per engine; default from config
GRAFANA ?= http://127.0.0.1:3001

help:  ## this list
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n", $$1, $$2}'

setup:  ## create .venv, install, create .env from the example if missing
	uv venv --python 3.12 -q .venv
	uv pip install -q --python $(PY) -e '.[dev]'
	@test -f .env || cp .env.example .env
	@echo "edit .env with your OPENROUTER_API_KEY, then: make check"

check:  ## key, OTel collector, MCP server, Jev and LLM reachability
	$(PY) -m po_agent.cli check

run-llm:  ## one baseline run:  make run-llm INTENT=examples/example-intent.md
	$(PY) -m po_agent.cli run --variant llm --intent $(INTENT)

run-hybrid:  ## one hybrid run:    make run-hybrid INTENT=...
	$(PY) -m po_agent.cli run --variant hybrid --intent $(INTENT)

benchmark:  ## both variants RUNS times + judge + consistency:  make benchmark INTENT=... RUNS=10
	$(PY) -m po_agent.cli benchmark --intent $(INTENT) --runs $(RUNS) $(if $(REPEATS),--consistency $(REPEATS),)

test:  ## unit and graph-routing tests, offline
	PO_TELEMETRY=0 $(PY) -m pytest -q

lint:  ## ruff
	$(PY) -m ruff check .

graph:  ## the graph as Mermaid on stdout and as docs/data/graph.json, both generated from the code
	$(PY) -m po_agent.cli graph --json docs/data/graph.json

examples:  ## docs/data/examples.json: the recorded payloads shown on the docs' "On the wire" page (EXPERIMENT=results/<id>, default newest)
	$(PY) tools/genexamples.py $(if $(EXPERIMENT),--experiment $(EXPERIMENT),)

dashboard:  ## generate grafana/po-benchmark.json and push it to Grafana ($(GRAFANA))
	$(PY) tools/gendashboard.py --push $(GRAFANA)

.PHONY: help setup check run-llm run-hybrid benchmark test lint graph dashboard
