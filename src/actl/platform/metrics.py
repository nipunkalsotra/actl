"""§22 Prometheus metrics -- RED per HTTP endpoint plus the domain
counters/gauges the architecture names explicitly: decisions by verdict
and closed reason code, gate denials by gate, saga compensations, LLM
calls and cache hits, audit chain length, reconciliation lag.

Every label here is drawn from a small, closed set -- a `ReasonCode`
enum member, a gate name (G1-G7), a compensation code (C1/C2/C4_C5), an
HTTP route *template* (never the raw path), a method, a status code.
§28 P10 instruction 2 is explicit: never an order id, mandate id, user
text, SKU, trace id, or provider id -- any of those would make a single
time series' cardinality unbounded, defeating Prometheus's whole storage
model. `tests/unit/platform/test_metrics.py` asserts this by construction
(every declared metric's label names are checked against a denylist).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

REGISTRY = CollectorRegistry()

decisions_total = Counter(
    "actl_decisions_total",
    "Policy decisions by verdict and closed reason code",
    ["verdict", "reason"],
    registry=REGISTRY,
)

gate_denials_total = Counter(
    "actl_gate_denials_total",
    "Money Action Gate denials by gate",
    ["gate"],
    registry=REGISTRY,
)

compensations_total = Counter(
    "actl_compensations_total",
    "Saga compensations applied, by compensation code",
    ["compensation"],
    registry=REGISTRY,
)

llm_calls_total = Counter(
    "actl_llm_calls_total",
    "LLM completion calls attempted, by repair-loop attempt number",
    ["attempt"],
    registry=REGISTRY,
)

llm_cache_hits_total = Counter(
    "actl_llm_cache_hits_total",
    "LLM semantic-cache hits (no Groq API call made)",
    registry=REGISTRY,
)

chain_length = Gauge(
    "actl_chain_length",
    "Current audit chain length (max seq)",
    registry=REGISTRY,
)

reconciliation_lag_seconds = Gauge(
    "actl_reconciliation_lag_seconds",
    "Age in seconds of the oldest non-terminal order the reconciler found due, 0 if none",
    registry=REGISTRY,
)

http_requests_total = Counter(
    "actl_http_requests_total",
    "HTTP requests, by route template, method, and status code",
    ["route", "method", "status"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "actl_http_request_duration_seconds",
    "HTTP request duration in seconds, by route template and method",
    ["route", "method"],
    registry=REGISTRY,
)


def render() -> bytes:
    return generate_latest(REGISTRY)
