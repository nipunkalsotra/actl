# 0012 — The LLM never authorizes a purchase

Status: Accepted
Date: 2026-08-30

## Context

An autonomous buyer agent needs natural-language understanding somewhere
in its pipeline — turning "book me a sea-facing room in Goa under 3000 a
night" into a structured mandate draft, ranking candidate catalog items,
narrating an outcome back to the human. Given that language model, the
architecture has to decide exactly how much authority it gets: does it
ever get to decide that a specific charge should happen, or is its output
always downstream of a decision something else already made?

## Decision

The LLM participates at exactly three bounded, optional points (§17):
extracting a mandate draft from conversational text (U1), ranking catalog
candidates for narration (U2), and narrating a decision/outcome back to
the human (U3) — never inside the money path. `domain.policy.engine.
evaluate()` is a pure, LLM-free function (§10: "no I/O, no wall clock, no
randomness, no exceptions escape") and the Money Action Gate (§11) is the
single chokepoint every charge must pass through, built entirely from
`application`/`domain` code with no `LLMClient` dependency anywhere in
its call graph — `tests/architecture/test_boundaries.py` enforces this
by construction, not by convention. Every LLM call is schema-validated
against a Pydantic model with a two-attempt repair loop (§17.2), capped
at three calls per transaction, and sits behind a deterministic fallback
path that the money path never blocks on: a raw catalog match if
extraction fails, alphabetical/price ordering if ranking fails, a
templated response if narration fails.

## Consequences

- **A prompt injection cannot forge a purchase.** The worst a
  manipulated LLM response can do is produce a malformed mandate draft
  (rejected by schema validation, `ClarificationNeeded` returned) or a
  badly-ordered candidate list (the buyer still sees real catalog data,
  just poorly ranked) — it can never reach `evaluate()` or the gate with
  attacker-controlled authority, because neither ever consults it.
- **Groq being down is a degraded-narration incident, not an outage.**
  `llm_down` is one of the five §20.1 demo scenarios specifically to
  prove this: the deterministic fallback completes the same transaction
  Groq would have narrated, just with a templated response instead of a
  generated one.
- **Cost and latency are bounded and predictable** (§17's own table): at
  most three calls, each cache-checked first, each rate-limited and
  circuit-breaker-guarded — an LLM outage or a cost spike can never
  cascade into an unbounded retry loop against a paid API.

## Alternatives considered

- **LLM-assisted policy evaluation** (the model proposes a verdict, a
  human or a second check confirms it). Rejected outright: this is
  exactly the "language model as authorizer" shape §02's judged-bar
  traceability matrix and §17's own framing rule out — an LLM's output is
  probabilistic and unauditable in the way a `rule_trace` entry is not,
  and "explainable" (§30's own definition-of-done line) requires every
  decision to reduce to a deterministic rule trace, not a model's stated
  reasoning.
- **No LLM at all.** Rejected: the growth/conversational-UX value this
  system demonstrates (§17, §22.2 growth instrumentation) depends on
  natural-language intent extraction and narration; removing it entirely
  would satisfy the security property trivially but abandon a real
  capability the three-bounded-uses design achieves without compromising
  it.

## Relevant architecture section

§02 The judged bar — requirement traceability matrix; §17 LLM subsystem
— three bounded uses; §11 The Money Action Gate.
