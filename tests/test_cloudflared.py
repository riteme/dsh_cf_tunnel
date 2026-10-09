"""cloudflared preflight: resolution, install guidance, and version lookup."""

from __future__ import annotations

import json
import platform
import sys

import pytest

from dsh_cf_tunnel import cloudflared


def test_resolve_executable_accepts_an_explicit_path() -> None:
    """An explicit path is used as-is when it is executable."""
    assert cloudflared.resolve_executable(sys.executable) == sys.executable


@pytest.mark.parametrize("missing", ["/nonexistent/cloudflared", "definitely-not-a-real-binary-xyz"])
def test_resolve_executable_rejects_what_it_cannot_run(missing: str) -> None:
    """A missing path and a missing PATH entry both resolve to nothing."""
    assert cloudflared.resolve_executable(missing) is None


def test_install_help_names_the_latest_release_and_both_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message must be actionable without any other documentation."""
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Linux")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(cloudflared, "latest_cloudflared_version", lambda timeout=8.0: "2026.10.0")
    text = cloudflared.cloudflared_install_help("cloudflared")
    assert "'cloudflared' was not found in PATH" in text
    assert "latest release: 2026.10.0" in text
    assert "pkg.cloudflare.com/cloudflare-main.gpg" in text
    assert cloudflared.CLOUDFLARED_APT_SOURCE in text
    assert "releases/latest/download/cloudflared-linux-amd64" in text
    assert "linux/x86_64 -> cloudflared-linux-amd64" in text


@pytest.mark.parametrize(
    ("machine", "asset"),
    [("aarch64", "arm64"), ("armv7l", "arm"), ("i686", "386"), ("x86_64", "amd64")],
)
def test_arch_mapping(monkeypatch: pytest.MonkeyPatch, machine: str, asset: str) -> None:
    """Every supported machine maps onto its release asset suffix."""
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: machine)
    assert cloudflared.cloudflared_arch() == asset


def test_unknown_platform_falls_back_to_the_release_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unmapped platform still gets a usable pointer."""
    monkeypatch.setattr(cloudflared.platform, "system", lambda: "Windows")
    monkeypatch.setattr(cloudflared.platform, "machine", lambda: "AMD64")
    text = cloudflared.cloudflared_install_help("cloudflared")
    assert "releases/latest" in text
    assert "not mapped to a release asset" in text


def test_ensure_cloudflared_exits_with_guidance(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """A missing binary is a non-zero exit with instructions, not a traceback."""
    monkeypatch.setattr(cloudflared, "resolve_executable", lambda binary: None)
    monkeypatch.setattr(cloudflared, "cloudflared_install_help", lambda binary: "INSTALL-HERE")
    with pytest.raises(SystemExit) as exit_info:
        cloudflared.ensure_cloudflared("cloudflared")
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert "INSTALL-HERE" in captured.err
    assert captured.out == ""


def test_ensure_cloudflared_returns_the_resolved_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A present binary is returned unchanged."""
    monkeypatch.setattr(cloudflared, "resolve_executable", lambda binary: "/opt/cloudflared")
    assert cloudflared.ensure_cloudflared("cloudflared") == "/opt/cloudflared"


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
        cloudflared.urllib.request, "urlopen", lambda request, timeout=None: _Response({"tag_name": "2026.10.0"})
    )
    assert cloudflared.latest_cloudflared_version() == "2026.10.0"


def test_latest_version_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable API must not stop the install guidance."""

    def boom(request: object, timeout: float | None = None) -> None:
        raise OSError("no route to host")

    monkeypatch.setattr(cloudflared.urllib.request, "urlopen", boom)
    assert cloudflared.latest_cloudflared_version() is None


def test_tunnel_pattern_ignores_the_api_host() -> None:
    """cloudflared's own error lines embed api.trycloudflare.com; that is not a tunnel."""
    failure = 'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": context deadline exceeded'
    assert cloudflared.QUICK_TUNNEL_URL_PATTERN.search(failure) is None
    assert cloudflared.QUICK_TUNNEL_FAILURE_PATTERN.search(failure)
    box = "INF |  https://talking-bald-states-speaker.trycloudflare.com   |"
    assert cloudflared.QUICK_TUNNEL_URL_PATTERN.search(box).group(0) == (
        "https://talking-bald-states-speaker.trycloudflare.com"
    )


def test_host_page_patterns_exist_for_every_reserved_label() -> None:
    """Reserved Cloudflare hostnames never look like a tunnel."""
    for label in ("api", "www", "developers", "blog", "community", "support", "dash", "one", "update"):
        line = f"see https://{label}.trycloudflare.com for details"
        assert cloudflared.QUICK_TUNNEL_URL_PATTERN.search(line) is None


def test_real_machine_is_supported() -> None:
    """The test host itself must map to an asset, so the guidance is never generic here."""
    assert cloudflared.CLOUDFLARED_ARCHES.get(platform.machine().lower()) is not None
