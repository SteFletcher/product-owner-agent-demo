"""OpenTelemetry for the graph: one tracer, one meter, the attribute names, and the instruments.

Spans carry everything (ids, counts, costs, the decision evidence). Metrics carry only the
low-cardinality dimensions listed in `METRIC_DIMENSIONS`; anything else is dropped before it
reaches an instrument, so a run id or a prompt can never become a Prometheus label.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from .config import TelemetryConfig

# --- attribute names --------------------------------------------------------------------------

EXPERIMENT_ID = "experiment.id"
RUN_ID = "run.id"
VARIANT = "workflow.variant"
NODE_NAME = "graph.node.name"
NODE_TYPE = "graph.node.type"          # generative | bounded | retrieval | deterministic
NODE_ATTEMPT = "graph.node.attempt"
ENGINE_TYPE = "engine.type"            # llm | jev | none
ENGINE_PROVIDER = "engine.provider"
MODEL_NAME = "model.name"
OPERATION = "operation.name"
INPUT_TOKENS = "input.tokens"
OUTPUT_TOKENS = "output.tokens"
TOTAL_TOKENS = "total.tokens"
INPUT_COST = "input.cost"
OUTPUT_COST = "output.cost"
TOTAL_COST = "total.cost"
COST_SOURCE = "cost.source"
LATENCY_MS = "latency.ms"
RETRY_COUNT = "retry.count"
ERROR_TYPE = "error.type"
STATUS = "status"
MCP_SERVER = "mcp.server"
MCP_TOOL = "mcp.tool"
RESULT_COUNT = "result_count"
DECISION_COUNT = "decision.count"
DECISION_MEAN_CONFIDENCE = "decision.mean_confidence"
REQUEST_ID = "openrouter.id"

METRIC_DIMENSIONS = frozenset({VARIANT, NODE_NAME, NODE_TYPE, ENGINE_TYPE, ENGINE_PROVIDER,
                               MODEL_NAME, STATUS, MCP_TOOL, "direction", "reason"})

_MS_BUCKETS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 60000, 120000, 300000]


def metric_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Keep only the dimensions allowed on a metric."""
    return {k: v for k, v in attrs.items() if k in METRIC_DIMENSIONS and v is not None}


class Telemetry:
    """Holds the providers and instruments. One per process; `shutdown()` flushes everything."""

    def __init__(self, config: TelemetryConfig):
        self.config = config
        self.enabled = config.enabled and os.environ.get("PO_TELEMETRY", "1") != "0"
        # One instance id per process: cumulative counters then never reset inside a series, so a
        # dashboard can read each process's final value with max_over_time and sum across runs.
        resource = Resource.create({"service.name": config.service_name, "service.namespace": "benchmarks",
                                    "service.instance.id": uuid.uuid4().hex[:12]})
        self._tracer_provider = TracerProvider(resource=resource)
        self._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{config.otlp_endpoint.rstrip('/')}/v1/metrics"),
                export_interval_millis=int(config.metric_export_interval_s * 1000))]
            if self.enabled else [],
            views=[View(instrument_name="po.*.duration",
                        aggregation=ExplicitBucketHistogramAggregation(_MS_BUCKETS))],
        )
        if self.enabled:
            self._tracer_provider.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{config.otlp_endpoint.rstrip('/')}/v1/traces")))
        self.tracer = self._tracer_provider.get_tracer("po_agent")
        meter = self._meter_provider.get_meter("po_agent")
        self.node_duration = meter.create_histogram("po.node.duration", unit="ms")
        self.node_tokens = meter.create_counter("po.node.tokens")
        self.node_cost = meter.create_counter("po.node.cost", unit="USD")
        self.inference_duration = meter.create_histogram("po.inference.duration", unit="ms")
        self.inference_calls = meter.create_counter("po.inference.calls")
        self.inference_tokens = meter.create_counter("po.inference.tokens")
        self.inference_cost = meter.create_counter("po.inference.cost", unit="USD")
        self.inference_retries = meter.create_counter("po.inference.retries")
        self.mcp_duration = meter.create_histogram("po.mcp.duration", unit="ms")
        self.run_duration = meter.create_histogram("po.run.duration", unit="ms")
        self.run_outcome = meter.create_counter("po.run.outcome")

    @contextlib.contextmanager
    def span(self, name: str, attrs: dict[str, Any] | None = None,
             kind: SpanKind = SpanKind.INTERNAL) -> Iterator[Span]:
        with self.tracer.start_as_current_span(name, kind=kind) as span:
            if attrs:
                span.set_attributes({k: v for k, v in attrs.items() if v is not None})
            try:
                yield span
            except Exception as e:
                span.record_exception(e)
                span.set_attribute(ERROR_TYPE, type(e).__name__)
                span.set_status(Status(StatusCode.ERROR, str(e)[:200]))
                raise
            else:
                span.set_status(Status(StatusCode.OK))

    def record_inference(self, attrs: dict[str, Any], latency_ms: float, input_tokens: int,
                         output_tokens: int, cost: float | None, retries: int, ok: bool) -> None:
        dims = metric_attrs({**attrs, STATUS: "ok" if ok else "error"})
        self.inference_duration.record(latency_ms, dims)
        self.inference_calls.add(1, dims)
        self.inference_tokens.add(input_tokens, {**dims, "direction": "input"})
        self.inference_tokens.add(output_tokens, {**dims, "direction": "output"})
        if cost is not None:
            self.inference_cost.add(cost, dims)
        if retries:
            self.inference_retries.add(retries, {**dims, "reason": attrs.get("reason", "retry")})

    def record_node(self, attrs: dict[str, Any], duration_ms: float, ok: bool, input_tokens: int = 0,
                    output_tokens: int = 0, cost: float | None = None) -> None:
        dims = metric_attrs({**attrs, STATUS: "ok" if ok else "error"})
        self.node_duration.record(duration_ms, dims)
        if input_tokens or output_tokens:
            self.node_tokens.add(input_tokens, {**dims, "direction": "input"})
            self.node_tokens.add(output_tokens, {**dims, "direction": "output"})
        if cost is not None:
            self.node_cost.add(cost, dims)

    def record_mcp(self, attrs: dict[str, Any], duration_ms: float, ok: bool) -> None:
        self.mcp_duration.record(duration_ms, metric_attrs({**attrs, STATUS: "ok" if ok else "error"}))

    def record_run(self, attrs: dict[str, Any], duration_ms: float, ok: bool) -> None:
        dims = metric_attrs({**attrs, STATUS: "ok" if ok else "error"})
        self.run_duration.record(duration_ms, dims)
        self.run_outcome.add(1, dims)

    def flush(self) -> None:
        self._tracer_provider.force_flush()
        self._meter_provider.force_flush()

    def shutdown(self) -> None:
        self.flush()
        self._tracer_provider.shutdown()
        self._meter_provider.shutdown()


def current_trace_ids() -> dict[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x")}


_global: Telemetry | None = None


def install(telemetry: Telemetry) -> Telemetry:
    """Register as the process-wide providers so libraries that use the global API join the trace."""
    global _global
    if _global is None:
        trace.set_tracer_provider(telemetry._tracer_provider)
        metrics.set_meter_provider(telemetry._meter_provider)
    _global = telemetry
    return telemetry
