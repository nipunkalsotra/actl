"""Typed error hierarchy (P8: every failure is a first-class outcome).

Platform-level only — domain-agnostic. Domain and application layers define
their own reason codes (e.g. the policy engine's closed ReasonCode enum) on
top of this, they do not subclass it into something that knows about money.
"""

from __future__ import annotations


class ActlError(Exception):
    """Base of every typed error in the system. Never raised directly."""

    reason_code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        reason_code: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if reason_code is not None:
            self.reason_code = reason_code
        self.details = details or {}


class ExternalServiceError(ActlError):
    """An untrusted external surface (§P6) misbehaved."""

    reason_code = "EXTERNAL_SERVICE_ERROR"


class CircuitOpenError(ExternalServiceError):
    """Raised by CircuitBreaker.call when the circuit is open."""

    reason_code = "CIRCUIT_OPEN"
