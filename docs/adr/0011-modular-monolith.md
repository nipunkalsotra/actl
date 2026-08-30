# 0011 — Modular monolith over microservices

Status: Accepted
Date: 2026-08-30

## Context

actl has clear internal seams — mandate/policy, catalog/quote, the Money
Action Gate, payments, the audit chain, the LLM subsystem, the agent
protocol — each with its own domain vocabulary and its own P-phase in the
build plan (§28). A team scaling this into a real product would likely
want to own and deploy some of these independently (the LLM subsystem in
particular, given its very different latency and cost profile). The
question this decision answers: does actl ship as one deployable process
(a "modular monolith," internally layered) or as a set of independently
deployed services from day one?

## Decision

actl is one deployable process — `actl.main:app` (the API) plus
`actl.worker` (webhook/reconciliation polling) — internally organized
into strict, import-linter-enforced layers (`domain` → `application` →
`infrastructure`/`interfaces`, §21) rather than network-separated
services. Every module boundary that *would* become a service boundary
in a distributed design is already a real Python module boundary today,
checked on every CI run by `.importlinter`'s five contracts (§21, §23.4):
domain stays pure with zero I/O imports, layers only point inward, and
only the gate/provider-factory may import a concrete payment adapter.

## Consequences

- **One transaction, one commitment.** The Money Action Gate's
  write-ahead audit entry (G7) and the payment provider call it guards
  (§11.2) share a single process's failure modes — no network partition
  between "we decided to charge" and "we recorded that we decided to
  charge" to reason about, which a service boundary here would introduce
  for zero benefit (§11's own G7 ordering guarantee depends on this).
- **One deploy, one rollback unit.** A reviewer (or a real operator)
  brings up the whole system with `make up && make migrate` and one
  `uvicorn`/`python -m actl.worker` pair — no service mesh, no per-service
  health matrix, matching the "100% free tier, no paid infrastructure"
  target runtime (§00 SCOPE).
- **The seams are real, so splitting later is a deployment change, not a
  redesign.** Because `application` never imports `infrastructure`
  backwards and `domain` never imports either, extracting (for example)
  the LLM subsystem into its own service later means moving
  `infrastructure/llm/` behind a network client that still implements the
  same `LLMClient` port (`application/ports.py`) — the application layer
  neither knows nor cares.

## Alternatives considered

- **Microservices from day one** (a mandate service, a payments service,
  an LLM service, communicating over HTTP/queues). Rejected: it multiplies
  the failure surface §20's ten failure modes already have to cover
  (network partition between services, in addition to every failure mode
  already listed) without adding any capability a reviewer or a real
  first deployment needs, and it costs real infrastructure (service
  discovery, inter-service auth) this build's free-tier target explicitly
  rules out.
- **A single undifferentiated module** (no `domain`/`application`/
  `infrastructure` split at all). Rejected: this is what actually makes
  "only the gate may import a payment provider" and "domain has zero I/O"
  enforceable by a CI-run contract (§23.4) rather than a code-review
  convention that erodes over time — the layering is what a modular
  monolith buys over a monolith that's merely one deployable.

## Relevant architecture section

§05 Container architecture and deployment topology; §21 Module
boundaries and dependency direction; §23.4 Architectural fitness
functions.
