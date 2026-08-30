"""OpenTelemetry spans (§22): "the trace id is the same one written into
the chain" -- not merely correlated with the audit `trace_id`, the exact
same 128-bit value in two encodings.

`platform.ids.ulid()` already produces a 128-bit payload (48-bit
timestamp + 80-bit randomness) Crockford-base32-encoded into 26
characters -- the identical width OpenTelemetry's own `TraceId` uses,
just hex-encoded instead. `encode_crockford`/`crockford_decode` are exact
inverses, so `trace_id_to_otel`/`otel_to_trace_id` below are a lossless
round trip: `otel_to_trace_id(trace_id_to_otel(t)) == t` for every ACTL
`trace_id` this build ever mints (see `test_trace_id_otel_round_trip`).

A custom `IdGenerator` is the only mechanism the OTel SDK exposes for
supplying a specific trace id: `Tracer.start_span()` calls
`id_generator.generate_trace_id()` exactly once, for a genuine root span
with no active parent context, and every descendant span thereafter
inherits that same trace id through ordinary parent-context propagation
-- no new mechanism of ours, just supplying the *first* id instead of a
random one. `transaction_span()` is therefore called exactly once, at a
transaction's outermost entry point (the gate, `handle_order_propose`,
webhook processing, worker ticks, ...); every nested `span()` call made
while it is active lands on the same trace automatically.

No specific exporter backend (OTLP/Jaeger/Tempo) is required anywhere in
§22 -- spans existing, with correct trace-id equality, is the actual
requirement, provable with `InMemorySpanExporter` in tests. Production/
demo use gets a real `TracerProvider` with no processor attached (spans
are created and immediately dropped) unless a caller opts into an
exporter explicitly, keeping this a genuinely free-tier, no-external-
collector build, consistent with every other port in this codebase.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator, RandomIdGenerator
from opentelemetry.trace import Span

from actl.platform.ids import crockford_decode, encode_crockford

TRACE_ID_PREFIX = "trc_"
_TRACE_ID_ULID_LENGTH = 26

_pending_trace_id: ContextVar[int | None] = ContextVar("_pending_trace_id", default=None)


def trace_id_to_otel(trace_id: str) -> int:
    """The exact 128-bit integer this `trace_id` encodes. Every id this
    build mints is `new_id(prefix)` = `f"{prefix}_{ulid()}"`, and `ulid()`
    is always exactly 26 Crockford characters regardless of which prefix
    a given call site chose -- so the payload is whatever follows the
    *last* underscore, not hardcoded to `trc_`. This matters because §14's
    wire-level `corr_id` (architecture §22: "corr_id equals the
    OpenTelemetry trace id") is minted client-side with its own prefix
    (e.g. `corr_...` in this repo's own test/demo clients), not `trc_`,
    yet still carries the identical 26-char ULID payload."""
    payload = trace_id.rpartition("_")[2]
    if len(payload) != _TRACE_ID_ULID_LENGTH:
        raise ValueError(f"not a ULID-shaped trace_id: {trace_id!r}")
    return crockford_decode(payload)


def otel_to_trace_id(otel_trace_id: int) -> str:
    """The exact audit `trace_id` string this OTel trace id decodes back
    to -- the inverse of `trace_id_to_otel`."""
    return TRACE_ID_PREFIX + encode_crockford(otel_trace_id, _TRACE_ID_ULID_LENGTH)


class _ActlIdGenerator(IdGenerator):
    """Span ids are always real random 64-bit values (§22 says nothing
    about span ids, only the trace id). Trace ids consult `_pending_
    trace_id` -- set exactly once, immediately before a transaction's
    root span starts -- falling back to a genuinely random trace id for
    any span opened outside a tracked ACTL transaction, so a stray span
    never raises or silently reuses a stale id."""

    def __init__(self) -> None:
        self._random = RandomIdGenerator()

    def generate_span_id(self) -> int:
        return self._random.generate_span_id()

    def generate_trace_id(self) -> int:
        pending = _pending_trace_id.get()
        if pending is not None:
            _pending_trace_id.set(None)
            return pending
        return self._random.generate_trace_id()


_tracer_provider: TracerProvider | None = None


def configure_tracing(exporter: SpanExporter | None = None) -> TracerProvider:
    """The process's one `TracerProvider`, using `_ActlIdGenerator`. Pass
    an explicit `exporter` (e.g. `InMemorySpanExporter()` in tests) to
    actually collect spans; omitted, spans are created (so `span()`/
    `transaction_span()` callers behave identically either way) but
    dropped immediately -- see this module's own docstring for why no
    default exporter is wired up."""
    global _tracer_provider
    provider = TracerProvider(id_generator=_ActlIdGenerator())
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    _tracer_provider = provider
    # `trace.set_tracer_provider` is first-call-wins -- the OTel API
    # silently ignores every call after the first per process, which would
    # make a second test's `configure_tracing(fresh_exporter)` a no-op if
    # `get_tracer` read the global. Calling it is still worthwhile (any
    # third-party instrumentation that reads the global sees something
    # coherent), but `get_tracer` below always uses this module's own
    # `_tracer_provider` directly, so re-configuring mid-process (every
    # test that wants its own `InMemorySpanExporter`) actually takes effect.
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(name: str = "actl") -> trace.Tracer:
    provider: trace.TracerProvider = _tracer_provider or trace.get_tracer_provider()
    return provider.get_tracer(name)


@contextlib.contextmanager
def transaction_span(
    name: str, trace_id: str, **attributes: str | int | float | bool
) -> Iterator[Span]:
    """Opens the ROOT span for one ACTL transaction. The OTel trace id
    this mints is exactly `trace_id_to_otel(trace_id)` -- call this once,
    at a transaction's outermost entry point; use `span()` for everything
    nested inside it. A `trace_id` that isn't ULID-shaped (e.g. a foreign
    client's freeform `corr_id` -- §14's wire envelope allows any non-
    empty string) falls back to a real random OTel trace id rather than
    raising: the same "never let an observability call break the money
    path" posture as `span()` below. The audit chain's own `trace_id`
    column is unaffected either way -- only the OTel mirroring degrades."""
    try:
        otel_trace_id = trace_id_to_otel(trace_id)
    except ValueError:
        otel_trace_id = None
    token = _pending_trace_id.set(otel_trace_id)
    tracer = get_tracer()
    try:
        with tracer.start_as_current_span(name, attributes=dict(attributes)) as current_span:
            yield current_span
    finally:
        _pending_trace_id.reset(token)


@contextlib.contextmanager
def span(name: str, **attributes: str | int | float | bool) -> Iterator[Span]:
    """A child span within whatever trace context is already active
    (normally opened by `transaction_span`). Safe to call with no active
    transaction span too -- it just starts a new, real-random-trace-id
    span rather than raising, the same "never let an observability call
    break the money path" posture `platform.redaction`/`platform.logging`
    already have."""
    tracer = get_tracer()
    with tracer.start_as_current_span(name, attributes=dict(attributes)) as current_span:
        yield current_span
