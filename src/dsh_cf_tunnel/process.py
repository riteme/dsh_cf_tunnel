"""Child-process ownership: output relays, cooperative shutdown, group signalling."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Sequence

from .errors import ShutdownRequested, StartupError
from .log import LOGGER

STOP_GRACE_SECONDS = 5.0


NOTABLE_LOG_LINE = re.compile(r"\b(err|error|fatal|fail|failed|wrn|warn)\b", re.IGNORECASE)


class Shutdown:
    """Turn SIGINT/SIGTERM into a cooperative stop so children are never orphaned.

    Children run in their own sessions, so a terminal Ctrl-C reaches only this
    program; an unhandled SIGTERM would otherwise kill it while cloudflared and
    dsh keep running and holding the port.
    """

    def __init__(self) -> None:
        self.requested = threading.Event()
        self.received: list[int] = []

    def install(self) -> None:
        """Register handlers for the signals that should shut the group down."""
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self._handle)

    def _handle(self, signum: int, _frame: object) -> None:
        self.received.append(signum)
        self.requested.set()

    def raise_if_requested(self) -> None:
        """Abort the current step when a signal already arrived."""
        if self.requested.is_set():
            raise ShutdownRequested(self.received[0])

    def wait(self, seconds: float) -> None:
        """Sleep, waking early when a signal arrives."""
        self.requested.wait(seconds)


class ProcessOutput:
    """Continuously drain one child's merged stdout/stderr, log it, and let callers await lines."""

    def __init__(self, process: subprocess.Popen, name: str, level: int = logging.DEBUG) -> None:
        self.process = process
        self.name = name
        self.level = level
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self._arrived = threading.Event()
        self._reader = threading.Thread(target=self._read, name=f"{name}-output", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        for raw in stream:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            with self._lock:
                self.lines.append(line)
            LOGGER.log(logging.WARNING if NOTABLE_LOG_LINE.search(line) else self.level, "%s: %s", self.name, line)
            self._arrived.set()
        self._arrived.set()

    def wait_for(
        self,
        pattern: re.Pattern[str],
        timeout: float,
        description: str,
        shutdown: Shutdown | None = None,
        fatal: re.Pattern[str] | None = None,
        fatal_grace: float = 0.0,
    ) -> re.Match[str]:
        """Block until a line matches, raising StartupError when the child dies or time runs out.

        @param fatal - a pattern whose lines mean the wait cannot succeed; the wait
        still continues for `fatal_grace` seconds, because the child may recover.
        """
        deadline = time.monotonic() + timeout
        failures: list[str] = []
        fatal_deadline: float | None = None
        cursor = 0
        while True:
            with self._lock:
                pending = self.lines[cursor:]
                cursor = len(self.lines)
            for line in pending:
                match = pattern.search(line)
                if match is not None:
                    return match
                if fatal is not None and fatal.search(line):
                    failures.append(line)
                    fatal_deadline = time.monotonic() + fatal_grace
            if shutdown is not None:
                shutdown.raise_if_requested()
            if self.process.poll() is not None and cursor >= len(self.lines):
                raise StartupError(f"{self.name} exited with code {self.process.returncode} before {description}")
            now = time.monotonic()
            if fatal_deadline is not None and now >= fatal_deadline and failures:
                raise StartupError(f"{self.name} could not {description}: {failures[-1]}")
            if now >= deadline:
                detail = f" (last failure: {failures[-1]})" if failures else ""
                raise StartupError(f"timed out after {timeout:g}s waiting for {description} from {self.name}{detail}")
            self._arrived.wait(0.2)
            self._arrived.clear()

    def tail(self, count: int = 5) -> str:
        """Render the last few lines, for error messages."""
        with self._lock:
            recent = self.lines[-count:]
        return "\n".join(f"    {line}" for line in recent)


class ProcessGroup:
    """Own every child process and stop them as a group on shutdown."""

    def __init__(self) -> None:
        self.members: list[ProcessOutput] = []

    def spawn(self, command: Sequence[str], name: str, level: int = logging.DEBUG) -> ProcessOutput:
        """Start one child in its own session so the whole group can be signalled."""
        LOGGER.debug("starting %s: %s", name, " ".join(command))
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        output = ProcessOutput(process, name, level)
        self.members.append(output)
        return output

    def stop_all(self) -> None:
        """Terminate children, escalating to SIGKILL when one ignores SIGTERM."""
        for member in reversed(self.members):
            process = member.process
            if process.poll() is not None:
                continue
            LOGGER.debug("stopping %s", member.name)
            _signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        for member in reversed(self.members):
            process = member.process
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                LOGGER.warning("%s ignored SIGTERM, sending SIGKILL", member.name)
                _signal_group(process, signal.SIGKILL)
                process.wait(timeout=STOP_GRACE_SECONDS)


def _signal_group(process: subprocess.Popen, sig: signal.Signals) -> None:
    """Signal a child's whole process group, tolerating an already-dead child."""
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(sig)
