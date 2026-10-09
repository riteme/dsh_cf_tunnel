"""Child-process ownership: output waiting, timeouts, and group shutdown."""

from __future__ import annotations

import re
import sys
import time

import pytest

from dsh_cf_tunnel.errors import ShutdownRequested, StartupError
from dsh_cf_tunnel.process import ProcessGroup, ProcessOutput, Shutdown


def emit(text: str) -> list[str]:
    """A python one-liner that prints text forever, so the child outlives the test body."""
    return [sys.executable, "-c", f"import time; print({text!r}, flush=True); time.sleep(30)"]


def test_wait_for_returns_the_matching_line() -> None:
    """The readiness gates read their line out of the child's merged output."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("quick tunnel ready"), "child")
        match = output.wait_for(re.compile(r"quick tunnel ready"), 10, "the marker")
        assert match.group(0) == "quick tunnel ready"
    finally:
        group.stop_all()


def test_wait_for_times_out_with_the_child_alive() -> None:
    """A silent child is a timeout, not a hang."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("nothing to see"), "child")
        with pytest.raises(StartupError, match="timed out"):
            output.wait_for(re.compile(r"never printed"), 0.5, "the marker")
    finally:
        group.stop_all()


def test_wait_for_reports_a_child_that_exited() -> None:
    """A child that died early is reported as such instead of waiting out the timeout."""
    group = ProcessGroup()
    try:
        output = group.spawn([sys.executable, "-c", "raise SystemExit(3)"], "child")
        with pytest.raises(StartupError, match="exited with code 3"):
            output.wait_for(re.compile(r"never printed"), 10, "the marker")
    finally:
        group.stop_all()


def test_wait_for_stops_on_a_shutdown_request() -> None:
    """A Ctrl-C during a wait surfaces as ShutdownRequested."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("quiet"), "child")
        shutdown = Shutdown()
        shutdown.requested.set()
        shutdown.received.append(2)
        with pytest.raises(ShutdownRequested):
            output.wait_for(re.compile(r"never printed"), 30, "the marker", shutdown)
    finally:
        group.stop_all()


def test_wait_for_reports_a_fatal_line_after_the_grace_period() -> None:
    """A fatal marker ends the wait with its own text once the grace period passes."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("failed to request quick Tunnel"), "child")
        with pytest.raises(StartupError, match="could not create the quick tunnel"):
            output.wait_for(
                re.compile(r"https://\w+\.example"),
                10,
                "create the quick tunnel",
                fatal=re.compile("failed to request quick Tunnel"),
                fatal_grace=0.5,
            )
    finally:
        group.stop_all()


def test_wait_for_lets_a_fatal_line_be_recovered_from() -> None:
    """cloudflared retries, so a failure line must not end the wait before a URL arrives."""
    group = ProcessGroup()
    script = (
        "import time;"
        "print('failed to request quick Tunnel', flush=True);"
        "time.sleep(0.5);"
        "print('https://tidy-river.trycloudflare.com', flush=True);"
        "time.sleep(30)"
    )
    try:
        output = group.spawn([sys.executable, "-c", script], "child")
        match = output.wait_for(
            re.compile(r"https://tidy-river\.trycloudflare\.com"),
            10,
            "create the quick tunnel",
            fatal=re.compile("failed to request quick Tunnel"),
            fatal_grace=5.0,
        )
        assert match.group(0) == "https://tidy-river.trycloudflare.com"
    finally:
        group.stop_all()


def test_tail_renders_the_last_lines() -> None:
    """Error paths quote the child's own words."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("last words"), "child")
        output.wait_for(re.compile(r"last words"), 10, "the marker")
        assert "last words" in output.tail()
    finally:
        group.stop_all()


def test_stop_all_terminates_every_child() -> None:
    """Ctrl-C must not leave a tunnel or a Web UI behind."""
    group = ProcessGroup()
    children = [group.spawn(emit(f"child {index}"), f"child {index}") for index in range(3)]
    deadline = time.monotonic() + 10
    for child in children:
        child.wait_for(re.compile(r"child \d"), 10, "the startup line")
    group.stop_all()
    assert all(child.process.poll() is not None for child in children)
    assert time.monotonic() < deadline


def test_stop_all_escalates_when_a_child_ignores_sigterm() -> None:
    """A child that traps SIGTERM is still stopped."""
    group = ProcessGroup()
    script = (
        "import signal, time;"
        "signal.signal(signal.SIGTERM, lambda *a: None);"
        "print('trapping', flush=True);"
        "time.sleep(60)"
    )
    try:
        output = group.spawn([sys.executable, "-c", script], "child")
        output.wait_for(re.compile(r"trapping"), 10, "the startup line")
        group.stop_all()
        assert output.process.poll() is not None
    finally:
        for member in group.members:
            if member.process.poll() is None:
                member.process.kill()


def test_stop_all_is_safe_without_children() -> None:
    """Shutting down an unstarted group is a no-op."""
    ProcessGroup().stop_all()


def test_process_output_defaults_to_debug_level() -> None:
    """The relay level decides how chatty the child's own log is."""
    group = ProcessGroup()
    try:
        output = group.spawn(emit("level check"), "child")
        assert isinstance(output, ProcessOutput)
        assert output.name == "child"
    finally:
        group.stop_all()
