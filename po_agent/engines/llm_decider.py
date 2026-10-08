"""The baseline's decision engine: the LLM asked exactly the System One questions, answering in
exactly the System One shape.

The state and the questions are rendered into one prompt. The answer schema is one small,
constant shape (a list of {id, p_yes, probabilities: [{option, p}]}), because providers reject a
large per-batch grammar in strict mode; ids and option keys are matched in code and the answers
are normalised by the same code that normalises Jev's. Chunking matches the Jev engine's, so both
variants make the same number of calls for the same node.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from ..config import JevConfig, LLMConfig
from ..pricing import total
from ..state import Answer
from .base import DecisionResult, LLMEngine, Usage, chunked, normalise

SYSTEM = """You are a careful decision engine. You are given a STATE and a set of typed QUESTIONS.
Answer every question only from the state. Do not write explanations.

Return one entry per question id, in the order given:
- noul: set p_yes to the probability (0 to 1) that the condition holds; leave probabilities empty.
- choice: set p_yes to null; give probabilities for every listed option key; they must sum to 1.
- score: set p_yes to null; give probabilities for every level, using the level index as the
  option ("0", "1", ...); they must sum to 1.

Calibrate honestly: use values near 0.5 when the state does not settle the question, and values
near 0 or 1 only when it clearly does."""


class OptionP(BaseModel):
    option: str
    p: float = Field(ge=0, le=1)


class QuestionAnswer(BaseModel):
    id: str
    p_yes: float | None
    probabilities: list[OptionP]


class DecisionAnswers(BaseModel):
    answers: list[QuestionAnswer]


def render(state: Any, questions: dict[str, dict]) -> str:
    state_text = state if isinstance(state, str) else json.dumps(
        state, ensure_ascii=False, indent=1, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o))
    lines = ["STATE:", state_text, "", "QUESTIONS:"]
    for qid, q in questions.items():
        lines.append(f"\n[id: {qid}] type={q['type']}")
        lines.append(f"instructions: {q['instructions']}")
        crit = q.get("criteria")
        if q["type"] == "noul" and crit:
            lines.append(f"true means: {crit.get('true')}; false means: {crit.get('false')}")
        elif q["type"] == "choice":
            for k, d in crit.items():
                lines.append(f"  option {k}: {d or ''}")
        elif q["type"] == "score":
            for i, lv in enumerate(crit):
                lines.append(f"  option {i}: {lv}")
    lines.append(f"\nReturn exactly {len(questions)} answers, one per id above.")
    return "\n".join(lines)


def to_raw(question: dict, answer: QuestionAnswer | None) -> dict:
    """The wire shape `normalise` expects; a missing answer becomes 'unsure' rather than a crash."""
    if answer is None:
        return {}
    if question["type"] == "noul":
        return {"noul": 0.5 if answer.p_yes is None else answer.p_yes}
    return {"probabilities": {o.option: o.p for o in answer.probabilities}}


class LLMDecisionEngine:
    engine_type = "llm"

    def __init__(self, llm: LLMEngine, llm_config: LLMConfig, jev_config: JevConfig):
        self.llm = llm
        self.model = llm_config.decision_model or llm_config.model
        self.temperature = llm_config.decision_temperature
        self.provider_name = llm.provider_name
        self.chunk_size = jev_config.chunk_size
        self.missing = 0      # answers the model failed to return, across the engine's life

    async def decide(self, *, state: Any, questions: dict[str, dict], purpose: str) -> DecisionResult:
        answers: dict[str, Answer] = {}
        usages: list[Usage] = []
        latency = 0.0
        retries = calls = 0
        model = self.model
        request_id = None
        for chunk in chunked(questions, self.chunk_size):
            if not chunk:
                continue
            r = await self.llm.generate(system=SYSTEM, prompt=render(state, chunk), schema=DecisionAnswers,
                                        purpose=purpose, model=self.model, temperature=self.temperature,
                                        max_tokens=4000)
            calls += 1
            retries += r.retries
            latency += r.latency_ms
            model, request_id = r.model, r.request_id
            usages.append(r.usage)
            got = {a.id: a for a in r.data.answers} if r.data else {}
            for qid, q in chunk.items():
                if qid not in got:
                    self.missing += 1
                answers[qid] = normalise(q, to_raw(q, got.get(qid)))
        usage = Usage(sum(u.input_tokens for u in usages), sum(u.output_tokens for u in usages),
                      total(u.cost for u in usages))
        return DecisionResult(answers=answers, model=model, provider=self.provider_name, usage=usage,
                              latency_ms=latency, retries=retries, calls=calls, request_id=request_id)
