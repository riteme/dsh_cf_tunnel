"""The loopback auth shim that keeps dsh's session cookie off the client's plate."""

from __future__ import annotations

import contextlib
import hmac
import http.client
import socket
import threading
import urllib.parse

from .log import LOGGER

# The loopback auth shim between the tunnel and dsh.
TOKEN_QUERY = "token"


INDEX_PATHS = frozenset({"/", "/index.html"})


INDEX_CONTENT_TYPE = "text/html; charset=utf-8"


HOP_BY_HOP_HEADERS = frozenset({"connection", "keep-alive", "proxy-connection"})


MAX_REQUEST_HEAD_BYTES = 65536


RELAY_CHUNK_BYTES = 65536


RELAY_JOIN_SECONDS = 5.0


REQUEST_HEAD_TIMEOUT_SECONDS = 30.0


SHIM_UPSTREAM_TIMEOUT_SECONDS = 30.0


class RequestHead:
    """One parsed HTTP/1.1 request head: request line plus ordered header fields."""

    def __init__(self, method: str, target: str, version: str, headers: list[tuple[str, str]]) -> None:
        self.method = method
        self.target = target
        self.version = version
        self.headers = headers

    @property
    def path(self) -> str:
        """Request path without its query string."""
        return urllib.parse.urlsplit(self.target).path

    @property
    def host(self) -> str:
        """Host authority the client asked for, defaulted for a direct loopback hit."""
        return self.header("host") or f"127.0.0.1:{self.target}"

    @property
    def is_upgrade(self) -> bool:
        """Whether this request asks to leave HTTP for another protocol (the API's WebSocket)."""
        return "upgrade" in self.header("connection", "").lower() or self.header("upgrade") is not None

    def header(self, name: str, default: str | None = None) -> str | None:
        """First value of one header field, case-insensitively."""
        wanted = name.lower()
        for field, value in self.headers:
            if field.lower() == wanted:
                return value
        return default


def read_request_head(client: socket.socket) -> tuple[bytes, bytes]:
    """Read one request head from a client socket, returning it with any bytes read past it."""
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = client.recv(RELAY_CHUNK_BYTES)
        if not chunk:
            return buffer, b""
        buffer += chunk
        if len(buffer) > MAX_REQUEST_HEAD_BYTES:
            raise OSError("request head exceeds the shim limit")
    head, _, remainder = buffer.partition(b"\r\n\r\n")
    return head + b"\r\n\r\n", remainder


def parse_request_head(head: bytes) -> RequestHead:
    """Parse a request head, tolerating the latin-1 bytes a header block may carry."""
    lines = head.decode("latin-1").split("\r\n")
    method, target, version = lines[0].split(" ", 2)
    headers = []
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers.append((name.strip(), value.strip()))
    return RequestHead(method, target, version, headers)


def relay(client: socket.socket, upstream: socket.socket) -> None:
    """Pump bytes between a client and its upstream until either side finishes."""

    def pump(source: socket.socket, sink: socket.socket) -> None:
        try:
            while True:
                data = source.recv(RELAY_CHUNK_BYTES)
                if not data:
                    break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                sink.shutdown(socket.SHUT_WR)

    helper = threading.Thread(target=pump, args=(upstream, client), daemon=True)
    helper.start()
    pump(client, upstream)
    helper.join(timeout=RELAY_JOIN_SECONDS)


def cookie_value_pair(cookie: str) -> str:
    """Reduce a Set-Cookie field to the `name=value` pair a Cookie request field carries."""
    return cookie.split(";", 1)[0].strip()


class AuthShim:
    """Front dsh with the session cookie so no client has to keep one across a redirect.

    dsh authenticates a browser by trading the launch token for an HttpOnly,
    ``SameSite=Strict``, authority-bound cookie during a ``303`` redirect, and then
    wants that cookie on the index and on every ``/api`` request. A client that does
    not carry the cookie across that redirect - an in-app WebView, a scanner
    handoff, or a navigation started by a browser extension - lands on the cleaned
    URL without a token and gets dsh's "authentication required" page.

    This loopback proxy sits between the tunnel and dsh: the token URL answers with
    the application and its ``Set-Cookie`` header directly, so there is no redirect
    to survive, and every request it forwards - static assets, ``/api``, and the
    ``/api/remote.mux`` WebSocket upgrade alike - carries the session cookie minted
    here instead of the one the client failed to keep.

    The index keeps dsh's own rule unless ``open_index`` is set: only the matching
    token - answered here directly - or the client's own cookie opens it, so learning
    the tunnel hostname alone is not enough.
    """

    def __init__(self, upstream_port: int, host: str = "127.0.0.1", open_index: bool = False) -> None:
        self.upstream_port = upstream_port
        self.host = host
        self.open_index = open_index
        self._token: str | None = None
        self._cookies: dict[str, str] = {}
        self._stop = threading.Event()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, 0))
        self._listener.listen(64)
        self.port = int(self._listener.getsockname()[1])
        self._lock = threading.Lock()
        self._acceptor = threading.Thread(target=self._accept, name="auth-shim", daemon=True)

    def start(self) -> None:
        """Begin accepting tunnel traffic."""
        self._acceptor.start()

    def arm(self, launch_token: str) -> None:
        """Hand over the launch token this shim trades for session cookies."""
        self._token = launch_token

    def stop(self) -> None:
        """Stop accepting traffic; in-flight connections finish on their own."""
        self._stop.set()
        with contextlib.suppress(OSError):
            self._listener.close()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_client, args=(client,), daemon=True).start()

    def _serve_client(self, client: socket.socket) -> None:
        """Serve one client connection: the token URL directly, everything else upstream."""
        upstream: socket.socket | None = None
        try:
            client.settimeout(REQUEST_HEAD_TIMEOUT_SECONDS)
            head, remainder = read_request_head(client)
            if not head:
                return
            request = parse_request_head(head)
            if self._serves_index(request):
                self._write_index(client, request)
                return
            client.settimeout(None)
            upstream = socket.create_connection((self.host, self.upstream_port), timeout=REQUEST_HEAD_TIMEOUT_SECONDS)
            upstream.sendall(self._forwarded_head(request))
            if remainder:
                upstream.sendall(remainder)
            relay(client, upstream)
        except (OSError, ValueError) as error:
            LOGGER.debug("auth shim: connection ended early: %s", error)
        finally:
            for opened in (upstream, client):
                if opened is not None:
                    with contextlib.suppress(OSError):
                        opened.close()

    def _serves_index(self, request: RequestHead) -> bool:
        """Whether this request is the token URL the shim answers itself."""
        if request.method not in ("GET", "HEAD") or request.path not in INDEX_PATHS:
            return False
        if self._token is None:
            return False
        tokens = urllib.parse.parse_qs(urllib.parse.urlsplit(request.target).query).get(TOKEN_QUERY, [])
        return len(tokens) == 1 and hmac.compare_digest(tokens[0], self._token)

    def _forwarded_head(self, request: RequestHead) -> bytes:
        """Rebuild the request head with the session cookie attached and HTTP/1.1 framing fixed."""
        headers = [(name, value) for name, value in request.headers if name.lower() not in HOP_BY_HOP_HEADERS]
        if self.open_index or request.path not in INDEX_PATHS:
            cookie = self._session_cookie(request.host)
            if cookie is not None:
                client_cookie = request.header("cookie")
                pair = cookie_value_pair(cookie)
                headers.append(("Cookie", f"{pair}; {client_cookie}" if client_cookie else pair))
        headers.append(("Connection", "Upgrade" if request.is_upgrade else "close"))
        # One request per upstream connection, so a forwarded reply always ends the
        # stream and a client that reuses connections still passes through here.
        lines = [f"{request.method} {request.target} {request.version}"]
        lines += [f"{name}: {value}" for name, value in headers]
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    def _session_cookie(self, host: str) -> str | None:
        """Return the session cookie for one authority, trading the token for it once."""
        with self._lock:
            cached = self._cookies.get(host)
            if cached is not None or self._token is None:
                return cached
            connection = http.client.HTTPConnection(
                self.host,
                self.upstream_port,
                timeout=SHIM_UPSTREAM_TIMEOUT_SECONDS,
            )
            try:
                connection.request("GET", f"/?{TOKEN_QUERY}={urllib.parse.quote(self._token)}", headers={"Host": host})
                response = connection.getresponse()
                response.read()
                cookie = response.getheader("set-cookie")
                if response.status != 303 or not cookie:
                    LOGGER.warning(
                        "auth shim: the token exchange for %s answered HTTP %s; " "forwarding without a session cookie",
                        host,
                        response.status,
                    )
                    return None
                self._cookies[host] = cookie
                LOGGER.debug("auth shim: minted a session cookie for %s", host)
                return cookie
            except OSError as error:
                LOGGER.warning("auth shim: the token exchange for %s failed: %s", host, error)
                return None
            finally:
                connection.close()

    def _write_index(self, client: socket.socket, request: RequestHead) -> None:
        """Answer the token URL with the application page and the session cookie, skipping the redirect."""
        cookie = self._session_cookie(request.host)
        if cookie is None:
            raise OSError("no session cookie is available for the token URL")
        body = self._render_index(request.host, cookie)
        LOGGER.debug("auth shim: served the index for %s without a redirect", request.host)
        head = [
            "HTTP/1.1 200 OK",
            f"content-type: {INDEX_CONTENT_TYPE}",
            f"content-length: {len(body)}",
            "cache-control: no-store",
            "referrer-policy: no-referrer",
            f"set-cookie: {cookie}",
            "connection: close",
        ]
        client.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        if request.method != "HEAD":
            client.sendall(body)

    def _render_index(self, host: str, cookie: str) -> bytes:
        """Fetch the authenticated index page from dsh."""
        connection = http.client.HTTPConnection(self.host, self.upstream_port, timeout=SHIM_UPSTREAM_TIMEOUT_SECONDS)
        try:
            connection.request("GET", "/", headers={"Host": host, "Cookie": cookie_value_pair(cookie)})
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise OSError(f"the index request answered HTTP {response.status}")
            return body
        finally:
            connection.close()
