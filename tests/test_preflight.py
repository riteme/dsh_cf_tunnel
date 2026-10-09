"""Preflight: both external tools are located, and neither is ever installed here."""

from __future__ import annotations

import json
import platform
import subprocess
import sys

import pytest

from dsh_cf_tunnel import preflight
from dsh_cf_tunnel.preflight import Tools


def test_resolve_executable_accepts_an_explicit_path() -> None:
    """An explicit path is used as-is when it is executable."""
    assert preflight.resolve_executable(sys.executable) == sys.executable


@pytest.mark.parametrize("missing", ["/nonexistent/cloudflared", "definitely-not-a-real-binary-xyz"])
def test_resolve_executable_rejects_what_it_cannot_run(missing: str) -> None:
    """A missing path and a missing PATH entry both resolve to nothing."""
    assert preflight.resolve_executable(missing) is None


def test_install_help_names_the_latest_release_and_both_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cloudflared message must be actionable without any other documentation."""
    monkeypatch.setattr(preflight.platform, "system", lambda: "Linux")
    monkeypatch.setattr(preflight.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(preflight, "latest_cloudflared_version", lambda timeout=8.0: "2026.10.0")
    text = preflight.cloudflared_install_help("cloudflared")
    assert "'cloudflared' was not found in PATH" in text
    assert "latest release: 2026.10.0" in text
    assert "pkg.cloudflare.com/cloudflare-main.gpg" in text
    assert preflight.CLOUDFLARED_APT_SOURCE in text
    assert "releases/latest/download/cloudflared-linux-amd64" in text
    assert "linux/x86_64 -> cloudflared-linux-amd64" in text


def test_dsh_install_help_offers_the_published_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dsh message names the real npm package and the existing-install escape hatch."""
    text = preflight.dsh_install_help("dsh")
    assert "'dsh' was not found in PATH" in text
    assert f"npm install -g {preflight.DSH_NPM_PACKAGE}" in text
    assert preflight.DSH_NPM_PACKAGE == "@deepseek-ai/dsh"
    assert preflight.DSH_REPOSITORY in text
    assert "--dsh-bin /path/to/dsh" in text


def test_dsh_install_help_never_suggests_the_unrelated_pypi_package() -> None:
    """`deepseek-harness` on PyPI is an unrelated project; only the GitHub repo is named.

    The repository URL legitimately contains that name, so the check is that no pip
    route to it is ever offered.
    """
    text = preflight.dsh_install_help("dsh")
    assert "pip install" not in text
    assert "pypi" not in text.lower()
    assert text.count(preflight.DSH_REPOSITORY) == 1


@pytest.mark.parametrize(
    ("machine", "asset"),
    [("aarch64", "arm64"), ("armv7l", "arm"), ("i686", "386"), ("x86_64", "amd64")],
)
def test_arch_mapping(monkeypatch: pytest.MonkeyPatch, machine: str, asset: str) -> None:
    """Every supported machine maps onto its release asset suffix."""
    monkeypatch.setattr(preflight.platform, "machine", lambda: machine)
    assert preflight.cloudflared_arch() == asset


def test_unknown_platform_falls_back_to_the_release_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmapped platform still gets a usable pointer."""
    monkeypatch.setattr(preflight.platform, "system", lambda: "Windows")
    monkeypatch.setattr(preflight.platform, "machine", lambda: "AMD64")
    text = preflight.cloudflared_install_help("cloudflared")
    assert "releases/latest" in text
    assert "not mapped to a release asset" in text


class _Response:
    """Minimal urlopen stand-in for the release lookup."""

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        """The response body."""
        return json.dumps(self.payload).encode()


def test_latest_version_reads_the_release_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GitHub tag is what the install message reports."""
    monkeypatch.setattr(
        preflight.urllib.request, "urlopen", lambda request, timeout=None: _Response({"tag_name": "2026.10.0"})
    )
    assert preflight.latest_cloudflared_version() == "2026.10.0"


def test_latest_version_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable API must not stop the install guidance."""

    def boom(request: object, timeout: float | None = None) -> None:
        raise OSError("no route to host")

    monkeypatch.setattr(preflight.urllib.request, "urlopen", boom)
    assert preflight.latest_cloudflared_version() is None


def test_ensure_cloudflared_exits_with_guidance(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """A missing binary is a non-zero exit with instructions, not a traceback."""
    monkeypatch.setattr(preflight, "resolve_executable", lambda binary: None)
    monkeypatch.setattr(preflight, "cloudflared_install_help", lambda binary: "INSTALL-CLOUDFLARED")
    with pytest.raises(SystemExit) as exit_info:
        preflight.ensure_cloudflared("cloudflared")
    assert exit_info.value.code == preflight.EXIT_MISSING_TOOL
    captured = capsys.readouterr()
    assert "INSTALL-CLOUDFLARED" in captured.err
    assert preflight.INSTALL_FOOTER in captured.err
    assert captured.out == ""


def test_ensure_dsh_exits_with_guidance(capsys: pytest.CaptureFixture) -> None:
    """The dsh check is the same contract as the cloudflared one."""
    with pytest.raises(SystemExit) as exit_info:
        preflight.ensure_dsh("/nonexistent/dsh")
    assert exit_info.value.code == preflight.EXIT_MISSING_TOOL
    captured = capsys.readouterr()
    assert "npm install -g @deepseek-ai/dsh" in captured.err
    assert preflight.INSTALL_FOOTER in captured.err


def test_ensure_executable_returns_a_present_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present binary is returned unchanged."""
    monkeypatch.setattr(preflight, "resolve_executable", lambda binary: "/opt/cloudflared")
    assert preflight.ensure_cloudflared("cloudflared") == "/opt/cloudflared"


def test_ensure_tools_resolves_both(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both tools reaching the startup sequence are real paths."""
    tools = preflight.ensure_tools(sys.executable, sys.executable)
    assert isinstance(tools, Tools)
    assert (tools.cloudflared, tools.dsh) == (sys.executable, sys.executable)


def test_ensure_tools_reports_every_missing_tool_at_once(capsys: pytest.CaptureFixture) -> None:
    """One run tells the user about both problems instead of one per attempt."""
    with pytest.raises(SystemExit) as exit_info:
        preflight.ensure_tools("/nonexistent/cloudflared", "/nonexistent/dsh")
    assert exit_info.value.code == preflight.EXIT_MISSING_TOOL
    err = capsys.readouterr().err
    assert "'/nonexistent/cloudflared' was not found in PATH" in err
    assert "'/nonexistent/dsh' was not found in PATH" in err
    assert "npm install -g @deepseek-ai/dsh" in err
    assert err.count(preflight.INSTALL_FOOTER) == 1


def test_ensure_tools_reports_only_the_missing_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A present tool is not reported."""
    monkeypatch.setattr(preflight, "resolve_executable", lambda binary: None if binary == "dsh" else sys.executable)
    with pytest.raises(SystemExit):
        preflight.ensure_tools("cloudflared", "dsh")
    err = capsys.readouterr().err
    assert "'dsh' was not found in PATH" in err
    assert "cloudflared:" not in err


def test_preflight_never_spawns_an_installer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """The guarantee the user asked for: a missing tool is reported, never installed.

    Any attempt to run a subprocess while the tools are missing would raise here.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"preflight must not run commands, got {args!r}")

    monkeypatch.setattr(preflight.subprocess, "run", refuse)
    monkeypatch.setattr(preflight.subprocess, "Popen", refuse)
    monkeypatch.setattr(preflight.subprocess, "call", refuse)
    monkeypatch.setattr(preflight.shutil, "which", lambda binary: None)
    monkeypatch.setattr(preflight.os, "access", lambda *args, **kwargs: False)
    with pytest.raises(SystemExit):
        preflight.ensure_tools("cloudflared", "dsh")
    assert "was not found in PATH" in capsys.readouterr().err


def test_executable_version_reads_the_first_line() -> None:
    """The version line names the install that is actually in use."""
    version = preflight.executable_version(sys.executable)
    assert version is not None and version.startswith("Python")


def test_executable_version_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool that cannot report a version is only logged about, never fatal."""

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("cannot execute")

    monkeypatch.setattr(preflight.subprocess, "run", boom)
    assert preflight.executable_version("/opt/tool") is None


def test_executable_version_ignores_a_failing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero --version is not a reason to stop the startup sequence."""
    completed = subprocess.CompletedProcess(args=["dsh", "--version"], returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: completed)
    assert preflight.executable_version("dsh") is None


def test_describe_tool_names_version_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The startup log line tells the user which installation is being driven."""
    monkeypatch.setattr(preflight, "executable_version", lambda binary: "0.2.0-rc.2")
    assert preflight.describe_tool("/usr/bin/dsh") == "0.2.0-rc.2 (/usr/bin/dsh)"
    monkeypatch.setattr(preflight, "executable_version", lambda binary: None)
    assert preflight.describe_tool("/usr/bin/dsh") == "/usr/bin/dsh"


def test_real_machine_is_supported() -> None:
    """The test host itself must map to an asset, so the guidance is never generic here."""
    assert preflight.CLOUDFLARED_ARCHES.get(platform.machine().lower()) is not None
