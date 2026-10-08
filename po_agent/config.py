"""Configuration: config/config.yaml with ${VAR} / ${VAR:-default} substitution, validated once.

Secrets are never values in the file; `api_key_env` names the variable to read at call time.
`Settings.effective()` is what gets written into every experiment directory: the same structure,
with no secrets in it, so a result can always be traced back to the models and thresholds used.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def substitute(value: Any) -> Any:
    """Replace ${VAR} and ${VAR:-default} inside strings, recursively through dicts and lists."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: substitute(v) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v) for v in value]
    return value


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str
    decision_model: str | None = None
    node_models: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.2
    decision_temperature: float = 0.0
    max_tokens: int = 6000
    prd_max_tokens: int = 24000      # construct_prd / refine_prd return plain Markdown, not JSON
    timeout_s: float = 180
    retries: int = 2

    def model_for(self, node: str) -> str:
        return self.node_models.get(node, self.model)

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


class JevConfig(BaseModel):
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api"
    endpoint: str = "/v1/systemone"
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = "typesafe/jev-1.13"
    timeout_s: float = 60
    retries: int = 2
    chunk_size: int = 12

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "") if self.api_key_env else ""


class JudgeConfig(BaseModel):
    model: str
    temperature: float = 0.0
    max_tokens: int = 4000


class KnowledgeConfig(BaseModel):
    transport: str = "stdio"
    command: list[str] = Field(default_factory=lambda: ["python", "-m", "po_agent.knowledge.server"])


class TelemetryConfig(BaseModel):
    enabled: bool = True
    otlp_endpoint: str = "http://127.0.0.1:4318"
    service_name: str = "product-owner-agent-demo"
    metric_export_interval_s: float = 5


class Thresholds(BaseModel):
    capability_selected: float = 0.5
    persona_selected: float = 0.5
    policy_applies: float = 0.5
    feature_overlaps: float = 0.6
    risk_investigate: float = 0.6
    risk_priority_investigate: int = 12
    outcome_delivered: float = 0.5
    validation_pass: float = 0.6


class Loops(BaseModel):
    risk_attempts: int = 2
    requirement_attempts: int = 3
    prd_attempts: int = 2


class BenchmarkConfig(BaseModel):
    results_dir: str = "results"
    runs: int = 1
    consistency_repeats: int = 5
    judge_both_orders: bool = True


class Settings(BaseModel):
    llm: LLMConfig
    jev: JevConfig
    judge: JudgeConfig
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    loops: Loops = Field(default_factory=Loops)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)
    config_path: str = ""
    pricing_path: str = ""

    def effective(self) -> dict[str, Any]:
        """The configuration as used, with every ${VAR} resolved and no secret values."""
        data = self.model_dump()
        data["llm"]["api_key_set"] = bool(self.llm.api_key)
        data["jev"]["api_key_set"] = bool(self.jev.api_key)
        return data


def load_settings(path: str | Path | None = None, pricing_path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("PO_CONFIG") or ROOT / "config" / "config.yaml")
    pricing = Path(pricing_path or os.environ.get("PO_PRICING") or ROOT / "config" / "pricing.yaml")
    raw = substitute(yaml.safe_load(config_path.read_text()) or {})
    return Settings(**raw, config_path=str(config_path), pricing_path=str(pricing))
