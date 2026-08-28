"""JSON structured logging (§22). Configured once at process start; every
subsequent structlog.get_logger() call shares the same processor chain."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from actl.platform.redaction import redaction_processor


def configure_logging(*, level: str = "INFO", json_format: bool = True) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
