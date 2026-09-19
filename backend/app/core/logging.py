"""
Structured logging with structlog.

Instead of free-text lines like "uploaded file for tenant 42", every log line is
an event name plus key/value fields:

    event="document.ingested" document_id=... chunks=37 duration_ms=2140

In production (LOG_JSON=true) each line is a JSON object, which log tools
(Loki, Datadog, CloudWatch...) can filter and aggregate — e.g. "p95 duration_ms of
document.ingested per tenant". In dev you get coloured, readable console output.

Privacy rule (plan section 14): never log document text, prompts, answers or
secrets. Log IDs, counts and timings only.
"""

import logging
import sys

import structlog

from app.core.config import get_settings


def configure_logging() -> None:
    settings = get_settings()

    shared_processors: list[structlog.typing.Processor] = [
        # Pulls in fields bound with structlog.contextvars.bind_contextvars(),
        # e.g. the request_id our middleware binds for every HTTP request.
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    return structlog.get_logger(name)
