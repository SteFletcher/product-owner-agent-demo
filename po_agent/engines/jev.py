"""The Jev engine: POST {base_url}{endpoint} in System One's wire shape, chunked, retried,
with one `inference.jev` span per call.

Jev's answers are probabilities, never text, so there is nothing to parse and nothing can come
back malformed; the only failures are transport ones. Points at OpenRouter by default and at a
local Jev-shaped server (Jev-Omni on :8200) by configuration.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .. import telemetry as T
from ..config import JevConfig
from ..pricing import Pricing, total
from ..state import Answer
from .base import DecisionResult, Usage, chunked, normalise

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def _jsonable(o):
    return o.model_dump() if hasattr(o, "model_dump") else str(o)


class JevEngine:
    engine_type = "jev"

    def __init__(self, config: JevConfig, pricing: Pricing, telemetry: T.Telemetry,
                 client: httpx.AsyncClient | None = None):
        self.config = config
        self.pricing = pricing
        self.telemetry = telemetry
        self.provider_name = config.provider
        self.model = config.model
        self.chunk_size = config.chunk_size
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        self.client = client or httpx.AsyncClient(base_url=config.base_url, timeout=config.timeout_s,
                                                  headers=headers)

    async def decide(self, *, state: Any, questions: dict[str, dict], purpose: str) -> DecisionResult:
        answers: dict[str, Answer] = {}
        usages: list[Usage] = []
        latency = 0.0
        retries = calls = 0
        request_id = None
        state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=_jsonable)
        for chunk in chunked(questions, self.chunk_size):
            if not chunk:
                continue
            out, ms, tries = await self._call(state_text, chunk, purpose)
            calls += 1
            retries += tries
            latency += ms
            request_id = out.get("id", request_id)
            raw = out.get("answers") or {}
            for qid, q in chunk.items():
                answers[qid] = normalise(q, raw.get(qid) or {})
            usages.append(self._usage(out))
        usage = Usage(sum(u.input_tokens for u in usages), sum(u.output_tokens for u in usages),
                      total(u.cost for u in usages))
        return DecisionResult(answers=answers, model=self.model, provider=self.provider_name,
                              usage=usage, latency_ms=latency, retries=retries, calls=calls,
                              request_id=request_id)

    async def _call(self, state: str, questions: dict[str, dict], purpose: str) -> tuple[dict, float, int]:
        body = {"model": self.model, "state": state, "questions": questions}
        attrs = {T.ENGINE_TYPE: "jev", T.ENGINE_PROVIDER: self.provider_name, T.MODEL_NAME: self.model,
                 T.OPERATION: purpose}
        last_error: Exception | None = None
        retries = 0
        for attempt in range(self.config.retries + 1):
            with self.telemetry.span("inference.jev", {**attrs, T.RETRY_COUNT: retries,
                                                       T.DECISION_COUNT: len(questions)},
                                     kind=T.SpanKind.CLIENT) as span:
                started = time.perf_counter()
                try:
                    r = await self.client.post(self.config.endpoint, json=body)
                    latency = (time.perf_counter() - started) * 1000
                    if r.status_code in RETRYABLE:
                        raise httpx.HTTPStatusError(f"{r.status_code}: {r.text[:300]}",
                                                    request=r.request, response=r)
                    r.raise_for_status()
                    out = r.json()
                    if "answers" not in out:
                        raise RuntimeError(f"no answers in response: {json.dumps(out)[:300]}")
                    usage = self._usage(out)
                    confs = [a.get("confidence") for a in out["answers"].values()
                             if isinstance(a, dict) and a.get("confidence") is not None]
                    span.set_attributes({
                        T.INPUT_TOKENS: usage.input_tokens, T.OUTPUT_TOKENS: usage.output_tokens,
                        T.TOTAL_TOKENS: usage.total_tokens, T.LATENCY_MS: round(latency, 1),
                        T.COST_SOURCE: usage.cost.source, T.REQUEST_ID: out.get("id", ""),
                        "served.model": out.get("model", self.model),
                        "served.provider": out.get("provider", ""),
                        "jev.reported_ms": float((out.get("usage") or {}).get("ms", 0) or 0)})
                    if confs:
                        span.set_attribute(T.DECISION_MEAN_CONFIDENCE, round(sum(confs) / len(confs), 4))
                    if usage.cost.known:
                        span.set_attribute(T.TOTAL_COST, usage.cost.value)
                    self.telemetry.record_inference(attrs, latency, usage.input_tokens,
                                                    usage.output_tokens, usage.cost.value, 0, True)
                    return out, latency, retries
                except (httpx.HTTPError, RuntimeError, json.JSONDecodeError) as e:
                    latency = (time.perf_counter() - started) * 1000
                    last_error = e
                    self.telemetry.record_inference({**attrs, "reason": type(e).__name__}, latency,
                                                    0, 0, None, 1, False)
                    span.record_exception(e)
                    span.set_attribute(T.ERROR_TYPE, type(e).__name__)
                    if attempt >= self.config.retries:
                        break
                    retries += 1
                    await asyncio.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"Jev call failed after {retries + 1} attempts ({purpose}): {last_error}")

    def _usage(self, out: dict) -> Usage:
        u = out.get("usage") or {}
        inp, outp = int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0))
        return Usage(inp, outp, self.pricing.cost(self.model, inp, outp, u.get("cost")))

    async def aclose(self) -> None:
        await self.client.aclose()
