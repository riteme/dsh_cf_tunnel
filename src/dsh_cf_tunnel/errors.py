"""Failures this package raises on its own terms."""

from __future__ import annotations

import signal


class StartupError(RuntimeError):
    """A child process failed to reach the state this program needs."""


class ShutdownRequested(Exception):
    """A signal asked the program to stop; the caller exits with 128 + signum."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"received {signal.Signals(signum).name}")
        self.signum = signum
