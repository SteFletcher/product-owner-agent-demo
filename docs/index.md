{{hero}}

## Read on

<ul class="contents">
<li><a href="executive-summary.html">Executive summary</a><span>The measured cost, speed, consistency and quality deltas, with the charts and what they do and do not show.</span></li>
<li><a href="architecture.html">Architecture</a><span>Google ADK on the outside, LangGraph inside, engines behind protocols, enterprise knowledge over MCP, OpenTelemetry throughout.</span></li>
<li><a href="graph.html">The graph</a><span>Eighteen nodes, three gated loops, and the node-by-node table of what each one does and which engine runs it.</span></li>
<li><a href="engine-selection.html">Engine selection</a><span>How every node was classified as generative, bounded, retrieval or deterministic, and the assumptions behind the Jev integration.</span></li>
<li><a href="examples.html">On the wire</a><span>What each engine is actually sent and what comes back: a generative prompt with its strict schema, the three System One question types, and one bounded node asked both ways.</span></li>
<li><a href="configuration.html">Configuration</a><span>Models, endpoints, thresholds, pricing, and the MCP contract a real knowledge server must meet.</span></li>
<li><a href="benchmark.html">Method</a><span>What is measured, how, and what is deliberately kept apart so the comparison stays honest.</span></li>
<li><a href="benchmark-results.html">Raw report</a><span>The report exactly as the benchmark printed it.</span></li>
</ul>

Run it yourself: [github.com/SteFletcher/product-owner-agent-demo](https://github.com/SteFletcher/product-owner-agent-demo) — `make setup`, `make check`, `make benchmark`.
