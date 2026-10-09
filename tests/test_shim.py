"""The auth shim, driven through its real socket against a stand-in for dsh."""

from __future__ import annotations

import http.client
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dsh_cf_tunnel.shim import AuthShim, RequestHead, parse_request_head

TOKEN = "launch-token-abc"
COOKIE = "dsh-auth-xyz=v1.payload.signature; Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict"


class FakeDsh:
    """Stand-in for dsh's index route and its token exchange, recording what it receives."""

    def __init__(self) -> None:
        self.token = TOKEN
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.exchanges = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # BaseHTTPRequestHandler spells this method with capitals
                headers = {name.lower(): value for name, value in self.headers.items()}
                owner.requests.append(("GET", self.path, headers))
                cookie = headers.get("cookie", "")
                requested = urllib.parse.urlsplit(self.path)
                if requested.path == "/" and requested.query == f"token={owner.token}":
                    owner.exchanges += 1
                    self.send_response(303)
                    self.send_header("location", "./")
                    self.send_header("set-cookie", COOKIE)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if requested.path in ("/", "/index.html"):
                    if "dsh-auth-" in cookie:
                        self._send(200, b"INDEX-PAGE")
                    else:
                        self._send(401, b"dsh web authentication required")
                    return
                self._send(200, f"ECHO {self.path} cookie={cookie}".encode())

            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", "text/plain")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        """Stop serving."""
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_dsh():
    """A recording stand-in for dsh."""
    server = FakeDsh()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture()
def shim(fake_dsh: FakeDsh):
    """An armed auth shim in front of the stand-in."""
    instance = AuthShim(fake_dsh.port)
    instance.arm(TOKEN)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


def get(port: int, target: str, headers: dict[str, str] | None = None) -> tuple[int, dict, bytes]:
    """One shim request, returned as status, lowercased headers, and body."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", target, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


def test_token_url_answers_the_app_without_a_redirect(shim: AuthShim, fake_dsh: FakeDsh) -> None:
    """The whole point: no 303 for a client to lose its cookie on."""
    status, headers, body = get(shim.port, f"/?token={TOKEN}")
    assert status == 200
    assert "location" not in headers
    assert body == b"INDEX-PAGE"
    assert headers["set-cookie"] == COOKIE
    assert headers["content-type"] == "text/html; charset=utf-8"


def test_forwarded_requests_carry_the_session_cookie(shim: AuthShim, fake_dsh: FakeDsh) -> None:
    """A client that never kept a cookie still reaches an authenticated upstream path."""
    status, _, body = get(shim.port, "/assets/app.js")
    assert status == 200
    assert b"cookie=dsh-auth-xyz=v1.payload.signature" in body


def test_the_token_is_exchanged_only_once(shim: AuthShim, fake_dsh: FakeDsh) -> None:
    """The session cookie is cached per authority instead of re-minted per request."""
    for _ in range(5):
        get(shim.port, "/assets/app.js")
    assert fake_dsh.exchanges == 1


def test_clients_own_cookie_is_preserved(shim: AuthShim, fake_dsh: FakeDsh) -> None:
    """A cookie-keeping browser keeps its cookie next to the shim's."""
    _, _, body = get(shim.port, "/assets/app.js", {"Cookie": "dsh-auth-old=v1.stale"})
    assert b"dsh-auth-xyz=v1.payload.signature" in body and b"dsh-auth-old=v1.stale" in body


def test_index_without_a_token_is_still_refused(shim: AuthShim) -> None:
    """Knowing the tunnel hostname alone must not open the application."""
    status, _, body = get(shim.port, "/")
    assert status == 401 and b"authentication required" in body


def test_a_wrong_token_is_refused(shim: AuthShim) -> None:
    """Only the launch token is traded, everything else falls through to dsh."""
    status, _, _ = get(shim.port, "/?token=not-the-token")
    assert status == 401


def test_index_paths_are_forwarded_without_the_shim_cookie(shim: AuthShim, fake_dsh: FakeDsh) -> None:
    """The index keeps dsh's rule; only non-index paths get the cookie injected."""
    get(shim.port, "/")
    forwarded = [request for request in fake_dsh.requests if request[1] == "/"]
    assert forwarded and "cookie" not in forwarded[-1][2]


def test_open_index_serves_a_token_less_index(fake_dsh: FakeDsh) -> None:
    """The documented weaker mode trades the token gate for client compatibility."""
    instance = AuthShim(fake_dsh.port, open_index=True)
    instance.arm(TOKEN)
    instance.start()
    try:
        status, _, body = get(instance.port, "/")
        assert status == 200 and body == b"INDEX-PAGE"
    finally:
        instance.stop()


def test_unreachable_upstream_is_reported_not_fatal() -> None:
    """A shim whose upstream is gone answers nothing but stays alive."""
    instance = AuthShim(9)  # discard port: nothing listens there
    instance.arm(TOKEN)
    instance.start()
    try:
        with pytest.raises((OSError, http.client.HTTPException)):
            get(instance.port, "/?token=" + TOKEN)
    finally:
        instance.stop()


def test_request_head_parses_case_insensitively() -> None:
    """Header lookup is case-insensitive and the path drops the query."""
    head = parse_request_head(b"GET /a?b=c HTTP/1.1\r\nHost: example.com\r\nUPGRADE: websocket\r\n\r\n")
    assert head.method == "GET" and head.path == "/a" and head.target == "/a?b=c"
    assert head.host == "example.com" and head.is_upgrade


def upgrade_head() -> RequestHead:
    """A WebSocket upgrade request as the API gateway sends it."""
    return parse_request_head(
        b"GET /api/remote.mux HTTP/1.1\r\n"
        b"Host: tunnel.example\r\n"
        b"Connection: keep-alive, Upgrade\r\n"
        b"Keep-Alive: timeout=5\r\n"
        b"Upgrade: websocket\r\n\r\n"
    )


def test_forwarded_head_rewrites_framing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hop-by-hop fields go and the connection intent is restated for one request per socket."""
    monkeypatch.setattr(AuthShim, "_session_cookie", lambda self, host: None)
    forwarded = AuthShim(1)._forwarded_head(upgrade_head()).decode()
    assert "keep-alive" not in forwarded.lower()
    assert forwarded.startswith("GET /api/remote.mux HTTP/1.1\r\n")
    assert "Connection: Upgrade\r\n" in forwarded
    assert forwarded.endswith("\r\n\r\n")


def test_forwarded_head_adds_the_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """The minted cookie rides along with whatever the client sent."""
    monkeypatch.setattr(AuthShim, "_session_cookie", lambda self, host: COOKIE)
    forwarded = AuthShim(1)._forwarded_head(upgrade_head()).decode()
    assert "Cookie: dsh-auth-xyz=v1.payload.signature\r\n" in forwarded


def test_request_head_defaults_the_host_for_a_direct_hit() -> None:
    """A loopback request without Host still resolves to an authority."""
    head = RequestHead("GET", "/", "HTTP/1.1", [])
    assert head.host == "127.0.0.1:/"
