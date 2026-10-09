"""End-to-end: boot a real quick tunnel and dsh, then behave like the clients that broke.

Opt in with ``DSH_CF_TUNNEL_E2E=1``; it needs network access, the ``dsh`` CLI, and
``cloudflared`` (override the latter with ``DSH_CF_TUNNEL_CLOUDFLARED``).
"""

from __future__ import annotations

import base64
import http.client
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import time
import urllib.parse

import pytest

pytestmark = pytest.mark.e2e

ENABLED = os.environ.get("DSH_CF_TUNNEL_E2E") == "1"
TIMEOUT = 30.0
BANNER_TIMEOUT = 240.0
TRANSIENT = (TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException)


@pytest.fixture(scope="module")
def tunnel(tmp_path_factory):
    """Boot the program against a real tunnel, yield its facts, and shut it down."""
    if not ENABLED:
        pytest.skip("set DSH_CF_TUNNEL_E2E=1 to run the tunnel end-to-end test")
    cloudflared = os.environ.get("DSH_CF_TUNNEL_CLOUDFLARED") or shutil.which("cloudflared")
    if cloudflared is None or not os.path.exists(cloudflared):
        pytest.skip("cloudflared is not installed")
    if shutil.which("dsh") is None:
        pytest.skip("the dsh CLI is not installed")

    workdir = tmp_path_factory.mktemp("e2e")
    log_path = workdir / "run.log"
    with open(log_path, "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dsh_cf_tunnel",
                "--cloudflared-bin",
                cloudflared,
                "--port",
                "0",
                "--log-level",
                "DEBUG",
            ],
            cwd=workdir,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + BANNER_TIMEOUT
        while time.monotonic() < deadline:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if "  Public URL : " in text:
                break
            if process.poll() is not None:
                raise AssertionError(f"the program exited early with {process.returncode}:\n{text[-2000:]}")
            time.sleep(1.0)
        else:
            raise AssertionError("timed out waiting for the banner")
        time.sleep(1.0)
        text = log_path.read_text(encoding="utf-8", errors="replace")
        yield {
            "text": text,
            "public": re.search(r"^  Public URL : (\S+)$", text, re.MULTILINE).group(1),
            "origin": re.search(r"^  Tunnel     : (\S+)$", text, re.MULTILINE).group(1),
            "dsh_port": int(re.search(r"dsh Web UI ready on loopback port (\d+)", text).group(1)),
            "shim_port": int(re.search(r"auth shim listening on 127\.0\.0\.1:(\d+)", text).group(1)),
        }
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()


def retry(call, attempts: int = 4):
    """Retry a tunnel request: the edge can drop a burst of fresh TLS handshakes."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except TRANSIENT:
            if attempt == attempts:
                raise
            time.sleep(2.0)


def get(host: str, target: str, headers: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    """One cookie-less HTTPS request to the tunnel."""

    def once() -> tuple[int, dict, bytes]:
        connection = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=ssl.create_default_context())
        try:
            connection.request("GET", target, headers=headers or {})
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()

    return retry(once)


def upgrade(host: str, target: str) -> int:
    """One WebSocket handshake through the tunnel, cookie-less."""

    def once() -> int:
        connection = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=ssl.create_default_context())
        try:
            connection.putrequest("GET", target, skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", host)
            connection.putheader("Upgrade", "websocket")
            connection.putheader("Connection", "Upgrade")
            connection.putheader("Sec-WebSocket-Key", base64.b64encode(os.urandom(16)).decode())
            connection.putheader("Sec-WebSocket-Version", "13")
            connection.putheader("Origin", f"https://{host}")
            connection.endheaders()
            return connection.getresponse().status
        finally:
            connection.close()

    return retry(once)


def test_token_url_serves_the_app_without_cookies(tunnel) -> None:
    """The regression the auth shim exists for: no redirect, no cookie jar, no 401."""
    parts = urllib.parse.urlsplit(tunnel["public"])
    status, headers, body = get(parts.netloc, parts.path + "?" + parts.query)
    assert status == 200
    assert "location" not in headers
    assert b"<title>DeepSeek Harness</title>" in body
    assert "set-cookie" in headers
    assert b'globalThis["__DSH_TRANSPORT__"] = {"ownsHost":true}' in body


def test_api_is_admitted_without_cookies(tunnel) -> None:
    """404 "not found" means the gateway admitted the request before resolving an endpoint."""
    host = urllib.parse.urlsplit(tunnel["origin"]).netloc
    status, _, body = get(host, "/api/")
    assert status == 404 and body == b"not found"


def test_the_same_request_is_refused_straight_to_dsh(tunnel) -> None:
    """The shim added the authentication, dsh alone still refuses it."""
    connection = http.client.HTTPConnection("127.0.0.1", tunnel["dsh_port"], timeout=TIMEOUT)
    try:
        connection.request("GET", "/api/", headers={"Host": urllib.parse.urlsplit(tunnel["origin"]).netloc})
        assert connection.getresponse().status == 401
    finally:
        connection.close()


def test_websocket_upgrade_survives_without_cookies(tunnel) -> None:
    """The API stream is what makes the UI live, and it must pass the shim too."""
    host = urllib.parse.urlsplit(tunnel["origin"]).netloc
    assert upgrade(host, "/api/remote.mux") == 101


def test_static_assets_pass_through_unchanged(tunnel) -> None:
    """Proxying must be byte exact for everything that is not the index."""
    host = urllib.parse.urlsplit(tunnel["origin"]).netloc
    parts = urllib.parse.urlsplit(tunnel["public"])
    status, _, index = get(host, parts.path + "?" + parts.query)
    assert status == 200
    match = re.search(rb"(?:src|href)=\"(\./assets/[^\"]+)\"", index)
    asset = match.group(1).decode().replace("./", "/") if match else "/favicon.svg"
    _, _, through_tunnel = get(host, asset)
    connection = http.client.HTTPConnection("127.0.0.1", tunnel["dsh_port"], timeout=TIMEOUT)
    try:
        connection.request("GET", asset)
        direct = connection.getresponse().read()
    finally:
        connection.close()
    assert through_tunnel == direct and through_tunnel


def test_a_wrong_token_is_still_refused(tunnel) -> None:
    """The token gate holds; only the real launch token is traded."""
    host = urllib.parse.urlsplit(tunnel["origin"]).netloc
    status, _, body = get(host, "/?token=not-the-launch-token")
    assert status == 401 and b"authentication required" in body


def test_a_cookie_keeping_client_still_works(tunnel) -> None:
    """Browsers that do keep the cookie are unaffected by the shim."""
    parts = urllib.parse.urlsplit(tunnel["public"])
    host = parts.netloc
    _, headers, _ = get(host, parts.path + "?" + parts.query)
    cookie = headers["set-cookie"].split(";", 1)[0]
    status, _, body = get(host, "/", {"Cookie": cookie})
    assert status == 200 and b"<title>DeepSeek Harness</title>" in body
