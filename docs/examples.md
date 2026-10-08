# On the wire: what each engine is sent

Every payload on this page is real. It was either sent during the recorded experiment or rebuilt by the
same code that sent it, from the run's own state (`make examples` writes `docs/data/examples.json` from
`results/<experiment>`). Nothing is paraphrased; long strings are cut and say so.

Two kinds of call leave the graph. **Generative** nodes ask an LLM for text or JSON that did not exist
before. **Bounded** nodes ask typed questions with a fixed answer space, and get probabilities back. The
baseline sends those questions to the LLM; the hybrid sends them to Jev. The questions are identical.

## The input

Everything starts from one Markdown document. `parse_intent` turns it into a structured brief; every
bounded question later is answered against that brief, not the raw text.

{{example:intent}}

## A generative node: the LLM writes

`parse_intent` is a normal chat completion over OpenRouter: a system prompt shared by every generative
node, a user prompt from `graph/prompts/parse_intent.md` with the intent substituted, and a strict JSON
schema derived from the `Brief` model. The model must return the whole object; the response is validated
against the same schema and a validation error is fed back for one corrective retry.

{{example:gen_request|gen_response}}

The two PRD-writing nodes are the exception: they return plain Markdown with a larger token budget
instead of a schema, because a 20-page document inside a JSON string is where models go wrong.

## The three question types

A bounded node never writes a prompt. It builds a dictionary of questions with three constructors from
`engines/base.py`, each in System One's wire shape:

```python
def noul(instructions, true=None, false=None):      # a yes/no with an optional gloss for each side
    q = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true, "false": false}
    return q

def choice(instructions, options: dict[str, str]):  # 2–20 named options, each with a description
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}

def score(instructions, levels: list[str]):         # 2–10 ordered levels
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}
```

Per-item questions put the item inside `instructions`, because System One answers every question in
isolation against the shared state. Below, one real question of each type from the recorded hybrid run,
and the answer Jev gave it after normalisation into the `Answer` model that every node reads.

{{example:q_noul|a_noul}}

A `noul` comes back as one probability. The node compares it with a threshold from `config.thresholds`
(here `capability_selected = 0.5`), so the policy lives in configuration, not in the engine or the node.

{{example:q_choice|a_choice}}

A `choice` comes back as a distribution over the option keys; `choice` is the arg-max, and `confidence`
is `1 − H(p) / ln K`, so a flat distribution scores 0 and a certain one scores 1.

{{example:q_score|a_score}}

A `score` comes back as a distribution over level indices. `score` is the expected index, and the node
turns it into a value on the scale it cares about (`level_value` maps `"0"…"4"` to 1–5).

## One bounded node, asked both ways

`assess_risks` asks four questions per risk: a `choice` for the category, two `score`s for likelihood and
impact, and a `noul` for whether it needs investigating before requirements are written. Questions are
sent twelve per call, so a run with twelve risks makes four calls to either engine. The payloads below
are the four questions for the first risk of the hybrid run's first attempt.

### To Jev

One `POST` to `/api/v1/systemone`. The body is the model, the state as one JSON string, and the question
dictionary. There is no prompt and no schema: the answers are probabilities, so nothing can come back
malformed, and the only failure mode is transport.

{{example:jev_request|jev_answers}}

The `derived` block is what the node does with the answers: category from the arg-max, likelihood and
impact as expected values, priority as their product, and the investigation flag from the probability
*or* the priority against two thresholds. That arithmetic is code, and it is the same code in both
variants.

### To the LLM

The baseline's decision engine renders the same state and the same four questions into one text prompt
and asks for one small, constant answer shape: a list of `{id, p_yes, probabilities}`. The shape is
constant because providers reject a large per-batch strict grammar; ids and option keys are matched back
in code, and the result is normalised by the same function that normalises Jev's answers. The system
prompt asks the model to calibrate honestly and to write no explanations.

{{example:llm_request|llm_answers}}

Two things to notice. The LLM's distributions are round numbers spread across every option, where Jev
puts most of the mass on one or two; that is why the consistency experiment compares decisions after
thresholds rather than raw probabilities. And the LLM's `confidence` on its scores is far lower than
Jev's because it hedged across the scale, not because its decision was different.

## What every bounded node asks

Built on the recorded run's final state by the same functions in `graph/questions.py`, and checked
against the number of decisions each node recorded.

{{census}}

The hybrid made these calls to Jev; the baseline made the same number to the LLM, with the same
questions in the same chunks. Everything else in the run (the eight generative calls, the two MCP
retrievals, the deterministic steps) is identical between the variants.
