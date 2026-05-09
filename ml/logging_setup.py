"""Configure structlog once, used by all ml/ modules."""

import logging

import structlog


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog. JSON output for production, console for dev."""
    logging.basicConfig(level=level, format="%(message)s")

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )


def configure_for_environment() -> None:
    """Detect env and configure logging appropriately. JSON in CI, console locally."""
    import os

    in_ci = os.environ.get("CI", "").lower() == "true"
    configure_logging(json_output=in_ci)
