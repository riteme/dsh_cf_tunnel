"""Package logging: progress goes to stderr, the banner owns stdout."""

from __future__ import annotations

import logging
import sys

# Progress logs go through the standard library only, keeping segno the single
# third-party dependency of this package.
LOGGER = logging.getLogger("dsh-cf-tunnel")


def configure_logging(level: str) -> None:
    """Send progress logs to stderr, keeping stdout reserved for the URL and QR code."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))
    LOGGER.handlers.clear()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(getattr(logging, level))
    LOGGER.propagate = False
