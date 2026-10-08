"""The Google ADK layer: a ProductOwnerAgent (BaseAgent) that owns the session and turns one
user message (the intent) into one model event (the PRD), by running the graph.

It has no model of its own on purpose: all reasoning is inside the graph, so this layer adds no
inference, no cost and no hidden prompt. It is what a UI or `adk web` would talk to.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, ClassVar

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import ConfigDict

from . import telemetry as T
from .config import Settings
from .pricing import Pricing
from .runner import RunRecord, new_experiment_id, run_variant

APP_NAME = "product-owner-agent-demo"


class ProductOwnerAgent(BaseAgent):
    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    settings: Settings
    pricing: Any
    telemetry: Any
    variant: str = "hybrid"
    out_dir: Path | None = None
    last_record: RunRecord | None = None

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        intent = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []))
        if not intent.strip():
            yield Event(author=self.name, content=types.Content(
                role="model", parts=[types.Part(text="Send the intent document as the message text.")]))
            return
        experiment_id = new_experiment_id()
        run_id = f"{self.variant}-01"
        out = (self.out_dir or Path(self.settings.benchmark.results_dir) / experiment_id) / self.variant / "run-01"
        record = await run_variant(self.settings, self.variant, intent, experiment_id, run_id,
                                   self.pricing, self.telemetry, out_dir=out)
        self.last_record = record
        text = record.prd if record.ok else f"Run failed: {record.error}"
        yield Event(author=self.name, content=types.Content(role="model", parts=[types.Part(text=text)]),
                    custom_metadata={"experiment_id": experiment_id, "run_id": run_id, "ok": record.ok,
                                     "out_dir": str(out), "trace_id": record.trace_id})


async def run_via_adk(settings: Settings, pricing: Pricing, telemetry: T.Telemetry, variant: str,
                      intent: str, out_dir: Path | None = None) -> tuple[RunRecord, dict]:
    agent = ProductOwnerAgent(name=f"product_owner_{variant}", description="Product Owner: intent -> PRD",
                              settings=settings, pricing=pricing, telemetry=telemetry, variant=variant,
                              out_dir=out_dir)
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=APP_NAME, user_id="benchmark")
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
    meta: dict = {}
    async for event in runner.run_async(user_id="benchmark", session_id=session.id,
                                        new_message=types.Content(role="user", parts=[types.Part(text=intent)])):
        if event.custom_metadata:
            meta = dict(event.custom_metadata)
    return agent.last_record, meta
