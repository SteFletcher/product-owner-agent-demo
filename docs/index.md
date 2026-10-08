# Product Owner agent: LLM vs LLM + Jev

An experiment that measures what changes when the bounded decisions inside an agentic Product
Owner workflow (intent document → PRD) are made by **Jev**, TypeSafe's System One decision model,
instead of an LLM, while the writing stays with the LLM.

- [Executive summary](executive-summary.md): measured cost, speed, consistency and quality deltas.
- [Architecture](architecture.md): Google ADK → LangGraph → engines → MCP → OpenTelemetry.
- [The graph](graph.md): 18 nodes, three gated loops, and the node-by-node engine table.
- [Engine selection](engine-selection.md): how each node was classified, and the assumptions.
- [Configuration](configuration.md): models, endpoints, thresholds, pricing, the MCP contract.
- [Benchmark method](benchmark.md): what is measured, how, and what is deliberately kept apart.
- [Benchmark results](benchmark-results.md): the raw report of the experiment behind the summary.

Source and instructions to run it yourself:
[github.com/SteFletcher/product-owner-agent-demo](https://github.com/SteFletcher/product-owner-agent-demo).
