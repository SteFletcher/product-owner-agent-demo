# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Freshly bootstrapped: no application code, build, lint or test tooling exists yet. Update this file when they are added.

## Repo and docs publishing

- GitHub: `SteFletcher/product-owner-agent-demo` (public), default branch `main`.
- `docs/` is published to GitHub Pages as static files (no build step) by `.github/workflows/pages.yml`.
  - Triggers on push to `main` touching `docs/**` or the workflow file, or manually: `gh workflow run pages`.
  - Pages source is set to "GitHub Actions" (not a branch). `docs/.nojekyll` disables Jekyll processing.
  - Live site: https://stefletcher.github.io/product-owner-agent-demo/
  - Check a deploy: `gh run list -w pages -L1`, then `gh run watch <id>`.
- If docs move to Markdown + a generator (MkDocs, Jekyll, etc.), add a build step before `upload-pages-artifact` and point its `path` at the build output.

## Local observability (OpenTelemetry)

A shared OTel stack runs in Docker on this machine (compose project `otel-stack`, defined in `~/Repos/devops/otel-stack`). Instrument the agent to export OTLP to it:

| Service | Endpoint |
|---|---|
| OTel Collector, OTLP gRPC | `http://127.0.0.1:4317` |
| OTel Collector, OTLP HTTP | `http://127.0.0.1:4318` |
| Collector health check | `http://127.0.0.1:13133/` |
| Grafana | `http://127.0.0.1:3001` |
| Tempo (traces) | `http://127.0.0.1:3200` |
| Loki (logs) | `http://127.0.0.1:3100` |
| Prometheus (metrics) | `http://127.0.0.1:9090` |

- Typical env: `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`, `OTEL_SERVICE_NAME=product-owner-agent-demo`.
- From inside a Docker container use `host.docker.internal` instead of `127.0.0.1`; ports are bound to localhost only.
- Check the collector: `curl -s http://127.0.0.1:13133/` and `docker logs otel-collector`. Pipeline config lives in the `otel-stack` repo, not here.
