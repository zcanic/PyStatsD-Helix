"""
Logging configuration using structlog.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

try:
    import structlog
    STRUCTLOG_AVAILABLE = True
except ImportError:
    STRUCTLOG_AVAILABLE = False

def configure_logging(level: str = "INFO", json_format: bool = False) -> None:
    """
    Configure logging for the application.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout
    )
    
    if STRUCTLOG_AVAILABLE and json_format:
        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer()
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
