"""Centralised logging configuration for the NewBad service.
It reads the LOG_LEVEL from the Settings singleton and configures a simple
stdout logger. In production you may replace this with JSON‑logging or a
structured logger.
"""

from __future__ import annotations

import logging
import sys

from .settings import settings

def configure_logging() -> None:
    """Configure the root logger according to ``settings.LOG_LEVEL``.

    The format includes timestamp, level, logger name and the message.
    """
    level_name = settings.LOG_LEVEL.upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

# Execute on import so that importing this module configures logging.
configure_logging()
