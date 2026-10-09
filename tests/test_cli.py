"""Command line surface and the pieces the startup order is built from."""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from dsh_cf_tunnel import cli
from dsh_cf_tunnel.errors import ShutdownRequested, StartupError


def test_defaults() -> None:
    """The defaults are what the README documents."""
    args = cli.parse_args([])
    assert args.port == 0
    assert args.dsh_bin == "dsh"
    assert args.cloudflared_bin == "cloudflared"
    assert args.ready_timeout == 60.0
    assert args.public_timeout == 120.0
    assert args.qr_style == "auto"
    assert args.qr_border == 2
    assert args.qr_png is None
    assert args.no_qr is False
    assert args.host_page is True
    assert args.auth_shim is True
    assert args.shim_open_index is False
    assert args.runtime_dir is None
    assert args.log_level == "INFO"


def test_flags_are_parsed() -> None:
    """Every documented flag reaches the namespace."""
    args = cli.parse_args(
        [
            "--port",
            "8123",
            "--dsh-bin",
            "/opt/dsh",
            "--cloudflared-bin",
            "/opt/cloudflared",
            "--ready-timeout",
            "5",
            "--public-timeout",
            "0",
            "--qr-style",
            "half",
            "--qr-border",
            "1",
            "--qr-png",
            "qr.png",
            "--no-qr",
            "--no-host-page",
            "--no-auth-shim",
            "--shim-open-index",
            "--runtime-dir",
            "tmp",
            "--log-level",
            "DEBUG",
        ]
    )
    assert args.port == 8123 and args.qr_style == "half" and args.public_timeout == 0
    assert args.no_qr and not args.host_page and not args.auth_shim
    assert args.shim_open_index and args.runtime_dir == "tmp" and args.log_level == "DEBUG"


def test_unknown_style_is_rejected(capsys: pytest.CaptureFixture) -> None:
    """argparse owns the style vocabulary."""
    with pytest.raises(SystemExit):
        cli.parse_args(["--qr-style", "fancy"])
    assert "invalid choice" in capsys.readouterr().err


def test_public_url_moves_the_token_to_the_tunnel() -> None:
    """Only the authority changes; path and query are preserved."""
    url = cli.public_url(
        "https://tidy-river.trycloudflare.com",
        "http://127.0.0.1:8123/?token=abc-123",
    )
    assert url == "https://tidy-river.trycloudflare.com/?token=abc-123"


def test_public_url_keeps_a_path() -> None:
    """A mounted path survives the rewrite."""
    url = cli.public_url("https://t.example", "http://127.0.0.1:1/harness/?token=xyz")
    assert url == "https://t.example/harness/?token=xyz"


def test_launch_token_is_extracted() -> None:
    """The token comes from dsh's own startup URL."""
    assert cli.launch_token("http://127.0.0.1:8123/?token=abc_123-XYZ") == "abc_123-XYZ"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8123/", "http://127.0.0.1:8123/?token=a&token=b"])
def test_missing_or_ambiguous_token_fails(url: str) -> None:
    """A URL without exactly one token cannot arm the shim."""
    with pytest.raises(StartupError):
        cli.launch_token(url)


def test_dsh_url_pattern_matches_the_startup_line() -> None:
    """The dsh banner line is parsed from the child's output."""
    line = "dsh web: dsh web: http://127.0.0.1:8123/?token=HTIMYnZxAOJvcPBd67KaF4EC3Y81sO7p5wNkM3oIkbo"
    assert cli.DSH_WEB_URL_PATTERN.search(line)


def test_find_free_port_returns_a_bindable_port() -> None:
    """The chosen port is really free."""
    port = cli.find_free_port()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


class _StatusServer(http.server.ThreadingHTTPServer):
    """A one-route server that answers a fixed status, for the probe helpers."""

    def __init__(self, status: int) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # BaseHTTPRequestHandler spells this method with capitals
                owner.seen.append(self.headers.get("Host"))
                body = b"body"
                self.send_response(status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.status = status
        self.seen: list[str | None] = []
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self) -> int:
        """The bound port."""
        return int(self.server_address[1])


@pytest.mark.parametrize("status", [200, 401, 403])
def test_trust_fence_probe_reports_the_status(status: int) -> None:
    """The probe passes the tunnel authority and reports what came back."""
    server = _StatusServer(status)
    try:
        assert cli.check_trust_fence(server.port, "tunnel.example") == status
        assert server.seen == ["tunnel.example"]
    finally:
        server.shutdown()
        server.server_close()


def test_trust_fence_probe_tolerates_a_dead_port() -> None:
    """A closed port is not an error, it is an unknown answer."""
    port = cli.find_free_port()
    assert cli.check_trust_fence(port, "tunnel.example") is None


def test_wait_until_reachable_accepts_any_answer_below_500() -> None:
    """A 401 or 403 still proves the edge routes the hostname."""
    server = _StatusServer(401)
    try:
        shutdown = cli.Shutdown()
        assert cli.wait_until_reachable(f"http://127.0.0.1:{server.port}/", 5.0, shutdown)
    finally:
        server.shutdown()
        server.server_close()


def test_wait_until_reachable_gives_up_and_says_so() -> None:
    """An unreachable URL is reported, not fatal."""
    server = _StatusServer(503)
    try:
        assert not cli.wait_until_reachable(f"http://127.0.0.1:{server.port}/", 0.5, cli.Shutdown())
    finally:
        server.shutdown()
        server.server_close()


def test_wait_until_reachable_honours_a_shutdown_request() -> None:
    """A Ctrl-C during the wait stops it immediately."""
    shutdown = cli.Shutdown()
    shutdown.requested.set()
    shutdown.received.append(2)
    with pytest.raises(ShutdownRequested):
        cli.wait_until_reachable("http://127.0.0.1:1/", 30.0, shutdown)


def test_wait_until_reachable_can_be_skipped() -> None:
    """--public-timeout 0 skips the reachability gate."""
    assert cli.wait_until_reachable("http://127.0.0.1:1/", 0, cli.Shutdown())


def test_banner_prints_the_three_urls(capsys: pytest.CaptureFixture) -> None:
    """stdout carries the URLs and nothing else."""
    args = cli.parse_args(["--no-qr"])
    cli.print_banner(
        "https://tidy-river.trycloudflare.com",
        "http://127.0.0.1:8123/?token=abc",
        "https://tidy-river.trycloudflare.com/?token=abc",
        args,
    )
    out = capsys.readouterr().out
    assert "Public URL : https://tidy-river.trycloudflare.com/?token=abc" in out
    assert "Tunnel     : https://tidy-river.trycloudflare.com" in out
    assert "Local URL  : http://127.0.0.1:8123/?token=abc" in out
    assert "Scan to open" not in out


def test_banner_prints_a_qr_code(capsys: pytest.CaptureFixture) -> None:
    """With a QR code requested the art is part of the banner."""
    args = cli.parse_args(["--qr-style", "plain"])
    cli.print_banner(
        "https://tidy-river.trycloudflare.com",
        "http://127.0.0.1:8123/?token=abc",
        "https://tidy-river.trycloudflare.com/?token=abc",
        args,
    )
    out = capsys.readouterr().out
    assert "Scan to open the dsh Web UI (plain style)" in out
    assert out.count("\n") > 10


def test_banner_writes_the_png(tmp_path, capsys: pytest.CaptureFixture) -> None:
    """--qr-png writes the file and says where."""
    target = tmp_path / "qr.png"
    args = cli.parse_args(["--no-qr", "--qr-png", str(target)])
    cli.print_banner("https://t.example", "http://127.0.0.1:1/?token=a", "https://t.example/?token=a", args)
    assert target.exists()
    assert f"QR image   : {target}" in capsys.readouterr().out
