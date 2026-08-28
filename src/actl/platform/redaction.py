"""Secret redaction for structured logs. Redaction is a filter, not discipline (§22)."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, cast

REDACTED = "***REDACTED***"

_SENSITIVE_KEY_MARKERS = (
    "secret",
    "password",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "signature",
)


def _looks_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _looks_sensitive(str(k)) else redact(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def redaction_processor(
    logger: object, method_name: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], redact(event_dict))
