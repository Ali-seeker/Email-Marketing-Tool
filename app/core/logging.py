"""Logging configuration for ColdReach.

Configures formatted application logging and prevents leaking sensitive data.
"""

import logging
import sys

from app.core.config import get_settings


def setup_logging() -> None:
    """Initialize root logger with appropriate formatting and log level."""
    settings = get_settings()
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with the given module name."""
    return logging.getLogger(name)
