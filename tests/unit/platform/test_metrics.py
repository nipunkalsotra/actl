"""§22 / §28 P10 instruction 2: the exact metrics the architecture names,
bounded-cardinality labels only. Structural checks here (names, label
sets, forbidden label names) are DB-free; value/increment correctness
against a real transaction lives in tests/integration/observability/
test_metrics_wiring.py.
"""

from __future__ import annotations

from actl.platform import metrics

# §28 P10 instruction 2, verbatim: never an order id, mandate id, user
# text, SKU, trace id, or provider id as a metric label.
_FORBIDDEN_LABEL_SUBSTRINGS = (
    "order_id",
    "order",
    "mandate_id",
    "mandate",
    "sku",
    "trace_id",
    "trace",
    "provider_id",
    "text",
    "user",
)

_ALL_METRICS = (
    metrics.decisions_total,
    metrics.gate_denials_total,
    metrics.compensations_total,
    metrics.llm_calls_total,
    metrics.llm_cache_hits_total,
    metrics.chain_length,
    metrics.reconciliation_lag_seconds,
    metrics.http_requests_total,
    metrics.http_request_duration_seconds,
)


def _label_names(metric: object) -> tuple[str, ...]:
    # prometheus_client stores declared label names on every collector,
    # including ones with none (an empty tuple) -- Gauge/Counter/Histogram
    # all expose this the same way.
    return tuple(metric._labelnames)  # type: ignore[attr-defined]


def test_every_declared_metric_has_the_actl_prefix() -> None:
    for metric in _ALL_METRICS:
        assert metric._name.startswith("actl_"), metric._name  # type: ignore[attr-defined]


def test_no_metric_uses_a_forbidden_high_cardinality_label() -> None:
    for metric in _ALL_METRICS:
        for label in _label_names(metric):
            assert label not in _FORBIDDEN_LABEL_SUBSTRINGS, (
                f"{metric._name} uses forbidden label {label!r}"  # type: ignore[attr-defined]
            )
    # `route` is a deliberate, explicit exception: it is a bounded path
    # *template* (e.g. "/agent/v1/messages"), never a raw URL/order id --
    # verified directly against main.py's own middleware, not just by
    # absence-of-name here.
    assert _label_names(metrics.http_requests_total) == ("route", "method", "status")


def test_decisions_total_labels_are_verdict_and_reason() -> None:
    assert _label_names(metrics.decisions_total) == ("verdict", "reason")


def test_gate_denials_total_label_is_gate_only() -> None:
    assert _label_names(metrics.gate_denials_total) == ("gate",)


def test_compensations_total_label_is_compensation_only() -> None:
    assert _label_names(metrics.compensations_total) == ("compensation",)


def test_chain_length_and_reconciliation_lag_have_no_labels() -> None:
    assert _label_names(metrics.chain_length) == ()
    assert _label_names(metrics.reconciliation_lag_seconds) == ()


def test_decisions_total_value_increments_by_exactly_one_per_call() -> None:
    before = metrics.decisions_total.labels(verdict="ALLOW", reason="OK")._value.get()
    metrics.decisions_total.labels(verdict="ALLOW", reason="OK").inc()
    after = metrics.decisions_total.labels(verdict="ALLOW", reason="OK")._value.get()
    assert after == before + 1


def test_gate_denials_total_value_increments_by_exactly_one_per_call() -> None:
    before = metrics.gate_denials_total.labels(gate="G4")._value.get()
    metrics.gate_denials_total.labels(gate="G4").inc()
    after = metrics.gate_denials_total.labels(gate="G4")._value.get()
    assert after == before + 1


def test_chain_length_gauge_set_reflects_exact_value() -> None:
    metrics.chain_length.set(42)
    assert metrics.chain_length._value.get() == 42
    metrics.chain_length.set(84)
    assert metrics.chain_length._value.get() == 84


def test_render_produces_prometheus_text_exposition_with_actl_metric_names() -> None:
    metrics.decisions_total.labels(verdict="ALLOW", reason="OK").inc()
    body = metrics.render().decode("utf-8")
    assert "actl_decisions_total" in body
    assert 'verdict="ALLOW"' in body
