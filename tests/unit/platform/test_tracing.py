"""§22 / §28 P10: the OpenTelemetry trace id must be the exact same
128-bit value as the audit `trace_id`, in two encodings -- not merely
correlated. These are the pure, DB-free properties; the full end-to-end
proof against real persisted audit rows lives in
tests/integration/observability/test_tracing_otel.py.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from actl.platform.ids import new_id
from actl.platform.tracing import (
    configure_tracing,
    otel_to_trace_id,
    span,
    trace_id_to_otel,
    transaction_span,
)


def test_trace_id_otel_round_trip() -> None:
    for _ in range(50):
        original = new_id("trc")
        assert otel_to_trace_id(trace_id_to_otel(original)) == original


def test_trace_id_to_otel_is_prefix_agnostic() -> None:
    """§14: the wire-level `corr_id` (minted `new_id("corr")` by this
    repo's own clients) carries the identical 26-char ULID payload as a
    `trc_`-prefixed id -- both must decode to the same OTel trace id."""
    ulid_payload = new_id("trc")[len("trc_") :]
    assert trace_id_to_otel(f"trc_{ulid_payload}") == trace_id_to_otel(f"corr_{ulid_payload}")


def test_trace_id_to_otel_rejects_non_ulid_shaped_ids() -> None:
    with pytest.raises(ValueError, match="not a ULID-shaped trace_id"):
        trace_id_to_otel("not-a-ulid-at-all")


def test_transaction_span_mints_exact_matching_otel_trace_id() -> None:
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)
    trace_id = new_id("trc")

    with transaction_span("use_case.test", trace_id), span("nested.child"):
        pass

    spans = exporter.get_finished_spans()
    assert {s.name for s in spans} == {"use_case.test", "nested.child"}
    expected = trace_id_to_otel(trace_id)
    assert all(s.context.trace_id == expected for s in spans)


def test_transaction_span_falls_back_to_random_for_malformed_trace_id() -> None:
    """Never let an observability call break the money path: a foreign,
    non-ULID-shaped trace_id (an external agent's freeform `corr_id`) must
    not raise -- it silently gets a real random OTel trace id instead."""
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)

    with transaction_span("use_case.test", "not-a-ulid-at-all"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].context.trace_id != 0


def test_two_separate_transaction_spans_get_different_trace_ids() -> None:
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)
    trace_id_a = new_id("trc")
    trace_id_b = new_id("trc")

    with transaction_span("use_case.a", trace_id_a):
        pass
    with transaction_span("use_case.b", trace_id_b):
        pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["use_case.a"].context.trace_id == trace_id_to_otel(trace_id_a)
    assert spans["use_case.b"].context.trace_id == trace_id_to_otel(trace_id_b)
    assert spans["use_case.a"].context.trace_id != spans["use_case.b"].context.trace_id


def test_nested_transaction_span_reuses_active_trace_not_new_id() -> None:
    """A `transaction_span` opened while already inside another one is
    inert (not a new root) -- OTel only mints a trace id for a genuine
    root span, so double-wrapping the same trace_id is always safe."""
    exporter = InMemorySpanExporter()
    configure_tracing(exporter)
    trace_id = new_id("trc")

    with transaction_span("outer", trace_id), transaction_span("inner_same_trace", trace_id):
        pass

    spans = exporter.get_finished_spans()
    expected = trace_id_to_otel(trace_id)
    assert all(s.context.trace_id == expected for s in spans)
