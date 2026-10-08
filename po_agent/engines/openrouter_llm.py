"""The LLM engine: OpenAI-compatible chat completions (OpenRouter by default) with strict JSON
schema output, retries, usage and cost, and one `inference.llm` span per attempt.

Works against any OpenAI-compatible server (LM Studio, Ollama, vLLM, OpenAI) by changing
`llm.base_url`; `usage.cost` is only reported by OpenRouter, elsewhere the pricing catalogue
applies.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .. import telemetry as T
from ..config import LLMConfig
from ..pricing import Pricing
from .base import LLMResult, Usage

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


class Truncated(RuntimeError):
    """The model hit max_tokens; the output is incomplete and retrying will not help."""


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic's JSON schema made strict: every property required, no additional properties."""
    schema = model.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for v in node.values():
                tighten(v)
        elif isinstance(node, list):
            for v in node:
                tighten(v)

    tighten(schema)
    return schema


class OpenRouterLLM:
    engine_type = "llm"

    def __init__(self, config: LLMConfig, pricing: Pricing, telemetry: T.Telemetry,
                 client: httpx.AsyncClient | None = None):
        self.config = config
        self.pricing = pricing
        self.telemetry = telemetry
        self.provider_name = config.provider
        self.client = client or httpx.AsyncClient(
            base_url=config.base_url, timeout=config.timeout_s,
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://github.com/SteFletcher/product-owner-agent-demo",
                     "X-Title": "product-owner-agent-demo"})

    async def generate(self, *, system: str, prompt: str, schema: type[BaseModel] | None,
                       purpose: str, model: str | None = None, temperature: float | None = None,
                       max_tokens: int | None = None, extra: dict[str, Any] | None = None) -> LLMResult:
        model = model or self.config.model
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        body: dict[str, Any] = {
            "model": model, "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "usage": {"include": True},
            **(extra or {}),
        }
        if schema is not None:
            # No provider.require_parameters: OpenRouter then finds no endpoint for several models
            # even though they honour the schema. The response is validated here regardless.
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "strict": True, "schema": strict_schema(schema)}}

        attrs = {T.ENGINE_TYPE: "llm", T.ENGINE_PROVIDER: self.provider_name, T.MODEL_NAME: model,
                 T.OPERATION: purpose}
        last_error: Exception | None = None
        retries = 0
        for attempt in range(self.config.retries + 1):
            with self.telemetry.span("inference.llm", {**attrs, T.RETRY_COUNT: retries},
                                     kind=T.SpanKind.CLIENT) as span:
                started = time.perf_counter()
                try:
                    r = await self.client.post("/chat/completions", json=body)
                    latency = (time.perf_counter() - started) * 1000
                    if r.status_code in RETRYABLE:
                        raise httpx.HTTPStatusError(f"{r.status_code}: {r.text[:300]}",
                                                    request=r.request, response=r)
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code} from {self.config.base_url}: {r.text[:500]}")
                    out = r.json()
                    if "error" in out:
                        raise RuntimeError(f"provider error: {json.dumps(out['error'])[:300]}")
                    text = out["choices"][0]["message"]["content"] or ""
                    usage = self._usage(out, model)
                    if out["choices"][0].get("finish_reason") == "length":
                        raise Truncated(f"output cut off at max_tokens={body['max_tokens']} "
                                        f"({usage.output_tokens} tokens out); head {text[:200]!r} "
                                        f"tail {text[-200:]!r}")
                    data = schema.model_validate_json(_json_only(text)) if schema else None
                    span.set_attributes({
                        T.INPUT_TOKENS: usage.input_tokens, T.OUTPUT_TOKENS: usage.output_tokens,
                        T.TOTAL_TOKENS: usage.total_tokens, T.LATENCY_MS: round(latency, 1),
                        T.COST_SOURCE: usage.cost.source, T.REQUEST_ID: out.get("id", ""),
                        "served.model": out.get("model", model), "served.provider": out.get("provider", "")})
                    if usage.cost.known:
                        span.set_attribute(T.TOTAL_COST, usage.cost.value)
                    self.telemetry.record_inference(attrs, latency, usage.input_tokens,
                                                    usage.output_tokens, usage.cost.value, 0, True)
                    return LLMResult(text=text, data=data, model=out.get("model", model),
                                     provider=out.get("provider"), usage=usage, latency_ms=latency,
                                     retries=retries, request_id=out.get("id"))
                except (httpx.HTTPError, ValidationError, json.JSONDecodeError, RuntimeError, KeyError) as e:
                    latency = (time.perf_counter() - started) * 1000
                    last_error = e
                    self.telemetry.record_inference({**attrs, "reason": type(e).__name__}, latency,
                                                    0, 0, None, 1, False)
                    span.record_exception(e)
                    span.set_attribute(T.ERROR_TYPE, type(e).__name__)
                    if isinstance(e, Truncated):
                        break   # a retry would be cut off at the same place; raise the budget instead
                    if isinstance(e, (ValidationError, json.JSONDecodeError)):
                        # Feed the error back so the next attempt can correct the shape.
                        body["messages"] = messages + [
                            {"role": "assistant", "content": text if "text" in locals() else ""},
                            {"role": "user", "content": f"That was not valid for the schema: {str(e)[:500]}. "
                                                        "Return only the corrected JSON."}]
                    if attempt >= self.config.retries:
                        break
                    retries += 1
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after {retries + 1} attempts ({purpose}): {last_error}")

    def _usage(self, out: dict, model: str) -> Usage:
        u = out.get("usage") or {}
        inp, outp = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
        reported = u.get("cost")
        return Usage(inp, outp, self.pricing.cost(out.get("model", model), inp, outp, reported))

    async def aclose(self) -> None:
        await self.client.aclose()


def _json_only(text: str) -> str:
    """Strip a ```json fence if a model added one despite the schema."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    return t.strip()
