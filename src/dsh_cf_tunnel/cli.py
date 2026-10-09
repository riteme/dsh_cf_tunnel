"""Command line entry point: parse flags, boot the tunnel and dsh, print the banner."""

from __future__ import annotations

import argparse
import http.client
import logging
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from .cloudflared import ensure_cloudflared, resolve_executable, start_cloudflared
from .errors import ShutdownRequested, StartupError
from .hostpage import write_host_page_patch
from .log import LOGGER, configure_logging
from .process import ProcessGroup, Shutdown
from .qr import QR_STYLES, qr_art, qr_columns, qr_matrix, resolve_qr_style, write_qr_png
from .shim import TOKEN_QUERY, AuthShim

DSH_WEB_URL_PATTERN = re.compile(r"http://\S*?[?&]token=[A-Za-z0-9_\-]+")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="dsh-cf-tunnel",
        description="Serve dsh --profile web through a Cloudflare quick tunnel and print a scannable QR code.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="loopback port for the dsh Web UI; 0 picks a free port and reuses it for the tunnel origin",
    )
    parser.add_argument(
        "--dsh-bin",
        default="dsh",
        help="dsh executable to launch; a bare name is searched in PATH",
    )
    parser.add_argument(
        "--cloudflared-bin",
        default="cloudflared",
        help="cloudflared executable; a bare name is searched in PATH",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for the quick-tunnel URL and then for the dsh Web UI URL",
    )
    parser.add_argument(
        "--public-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for the public URL to answer before printing the banner; 0 skips the check",
    )
    parser.add_argument(
        "--no-qr",
        action="store_true",
        help="print the public URL without rendering the terminal QR code",
    )
    parser.add_argument(
        "--qr-border",
        type=int,
        default=2,
        help="quiet-zone width of the printed QR code, in modules",
    )
    parser.add_argument(
        "--qr-style",
        default="auto",
        choices=list(QR_STYLES),
        help="terminal QR rendering: background paints square modules on black/white backgrounds, "
        "half uses two half-block module rows per text row, plain is the colorless variant, "
        "auto prefers background in a terminal and plain when the output is redirected",
    )
    parser.add_argument(
        "--qr-png",
        default=None,
        metavar="PATH",
        help="also write the QR code to PATH as a PNG image",
    )
    parser.add_argument(
        "--no-host-page",
        dest="host_page",
        action="store_false",
        help="do not patch dsh with the host-owned page flag; without it the tunneled page never "
        "counts as a loopback page, so dsh's Settings and Models pages report "
        '"settings are unavailable in this browser"',
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        metavar="PATH",
        help="directory holding the generated dsh patch files (default: a fresh temporary directory)",
    )
    parser.add_argument(
        "--shim-open-index",
        action="store_true",
        help="also let the auth shim serve dsh's index page to clients that present no token, so the "
        "tunnel hostname alone grants access; weaker, but survives clients that mangle the URL",
    )
    parser.add_argument(
        "--no-auth-shim",
        dest="auth_shim",
        action="store_false",
        help="keep the auth shim in front of dsh (default); disabling it lets the tunnel reach dsh "
        "directly, so every client must then keep dsh's session cookie across its 303 redirect",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging level for this program's own progress logs",
    )
    return parser.parse_args(argv)


def find_free_port() -> int:
    """Ask the OS for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def launch_token(dsh_url: str) -> str:
    """Extract the launch token from the URL dsh printed at startup."""
    tokens = urllib.parse.parse_qs(urllib.parse.urlsplit(dsh_url).query).get(TOKEN_QUERY, [])
    if len(tokens) != 1:
        raise StartupError(f"the dsh startup URL {dsh_url!r} carries no usable {TOKEN_QUERY} parameter")
    return tokens[0]


def start_dsh_web(
    group: ProcessGroup,
    binary: str,
    port: int,
    trusted_hosts: Sequence[str],
    timeout: float,
    shutdown: Shutdown,
    patch: str | None = None,
) -> str:
    """Start the dsh Web UI with the tunnel hostname trusted, and return its token URL."""
    resolved = resolve_executable(binary)
    if resolved is None:
        raise StartupError(f"dsh executable '{binary}' was not found in PATH; pass --dsh-bin /path/to/dsh")
    command = [resolved]
    if patch is not None:
        # --patch is a launcher flag, so it must precede the profile name: every
        # argument after `web` belongs to the web app's own parser.
        command += ["--patch", patch]
    command += [
        "--profile",
        "web",
        "--no-open",
        "--port",
        str(port),
        # --trusted-host is variadic in the web app, so it stays last: every
        # extra tunnel hostname goes here. A host:port entry matches exactly,
        # a port-less hostname matches any port.
        "--trusted-host",
        *trusted_hosts,
    ]
    output = group.spawn(command, name="dsh web", level=logging.INFO)
    match = output.wait_for(DSH_WEB_URL_PATTERN, timeout, "the dsh Web UI URL", shutdown)
    LOGGER.info("dsh Web UI ready on loopback port %s", port)
    return match.group(0)


def public_url(origin: str, dsh_url: str) -> str:
    """Rebuild the dsh token URL on the tunnel origin, preserving path and token."""
    parts = urllib.parse.urlsplit(dsh_url)
    host = urllib.parse.urlsplit(origin).netloc
    return urllib.parse.urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def wait_until_reachable(url: str, timeout: float, shutdown: Shutdown) -> bool:
    """Poll the public URL until the tunnel edge serves it.

    A freshly created quick tunnel needs a moment before its hostname resolves and
    the edge routes it, so a URL printed too early answers NXDOMAIN when scanned.
    @returns true once the edge answered below 500, false when it never did.
    """
    if timeout <= 0:
        return True
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        status: int | None = None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "dsh-cf-tunnel"})
            with urllib.request.urlopen(request, timeout=15) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        except (urllib.error.URLError, OSError) as error:
            LOGGER.debug("public URL attempt %s failed: %s", attempt, error)
        if status is not None and status < 500:
            LOGGER.info("public URL answered with HTTP %s after %s attempt(s)", status, attempt)
            return True
        shutdown.raise_if_requested()
        if time.monotonic() >= deadline:
            return False
        if attempt % 5 == 1:
            LOGGER.info("waiting for the tunnel edge to serve the public URL ...")
        shutdown.wait(2.0)


def check_trust_fence(port: int, tunnel_host: str) -> int | None:
    """Probe /api through the tunnel hostname: 401 means trusted, 403 means the fence rejects it."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        connection.request("GET", "/api/", headers={"Host": tunnel_host})
        return connection.getresponse().status
    except OSError as error:
        LOGGER.debug("trust-fence probe failed: %s", error)
        return None
    finally:
        connection.close()


def print_banner(origin: str, local_url: str, url: str, args: argparse.Namespace) -> None:
    """Print the public URL, the local fallback, and the QR code on stdout."""
    rule = "=" * 72
    lines = [
        rule,
        f"  Public URL : {url}",
        f"  Tunnel     : {origin}",
        f"  Local URL  : {local_url}",
    ]
    if args.qr_png:
        try:
            write_qr_png(url, args.qr_png)
            lines.append(f"  QR image   : {args.qr_png}")
        except ImportError:
            LOGGER.warning("segno is not installed; cannot write the QR image to %s", args.qr_png)
    lines.append(rule)
    if not args.no_qr:
        try:
            rows = qr_matrix(url, args.qr_border)
            style = resolve_qr_style(args.qr_style, rows)
            columns = qr_columns(rows, style)
            terminal_columns = shutil.get_terminal_size((80, 24)).columns
            if sys.stdout.isatty() and columns > terminal_columns:
                LOGGER.warning(
                    "the %s QR code is %s columns wide but the terminal has %s; "
                    "widen it, lower --qr-border, or pass --qr-style half",
                    style,
                    columns,
                    terminal_columns,
                )
            lines += [f"  Scan to open the dsh Web UI ({style} style):", "", qr_art(rows, style), ""]
        except ImportError:
            LOGGER.warning("segno is not installed; skipping the QR code (install it with: pip install segno)")
    # The banner is the whole point of the run, and stdout may be a pipe or a log
    # file where it would otherwise sit in the block buffer.
    print("\n".join(lines), flush=True)


def monitor(group: ProcessGroup, shutdown: Shutdown) -> int:
    """Wait until a child exits or a signal arrives, then report the outcome."""
    while not shutdown.requested.is_set():
        for member in group.members:
            code = member.process.poll()
            if code is not None:
                LOGGER.error("%s exited with code %s; shutting down", member.name, code)
                tail = member.tail()
                if tail:
                    LOGGER.error("last %s output:\n%s", member.name, tail)
                return 1 if code == 0 else code
        shutdown.wait(0.5)
    raise ShutdownRequested(shutdown.received[0])


def run(args: argparse.Namespace, shutdown: Shutdown) -> int:
    """Run the full startup sequence and supervise both children."""
    cloudflared = ensure_cloudflared(args.cloudflared_bin)
    try:
        version = subprocess.run(
            [cloudflared, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.debug("could not read the cloudflared version: %s", error)
        version = ""
    LOGGER.info("using cloudflared: %s", version or cloudflared)

    port = args.port if args.port > 0 else find_free_port()
    group = ProcessGroup()
    shim: AuthShim | None = None
    runtime_dir = args.runtime_dir
    remove_runtime_dir = False
    if args.host_page:
        try:
            if runtime_dir is None:
                runtime_dir = tempfile.mkdtemp(prefix="dsh-server-")
                remove_runtime_dir = True
            patch = write_host_page_patch(runtime_dir)
        except OSError as error:
            raise StartupError(
                f"could not write the host-owned page patch ({error}); pass --runtime-dir PATH or --no-host-page"
            ) from error
        LOGGER.info("the Web UI settings pages stay available remotely through %s", patch)
    else:
        patch = None
    try:
        tunnel_port = port
        if args.auth_shim:
            shim = AuthShim(port, open_index=args.shim_open_index)
            shim.start()
            tunnel_port = shim.port
            LOGGER.info("auth shim listening on 127.0.0.1:%s -> dsh on 127.0.0.1:%s", shim.port, port)
        origin = start_cloudflared(group, cloudflared, tunnel_port, args.ready_timeout, shutdown)
        tunnel_host = urllib.parse.urlsplit(origin).netloc
        local_url = start_dsh_web(
            group,
            args.dsh_bin,
            port,
            [tunnel_host],
            args.ready_timeout,
            shutdown,
            patch=patch,
        )
        url = public_url(origin, local_url)
        if shim is not None:
            shim.arm(launch_token(local_url))

        status = check_trust_fence(port, tunnel_host)
        if status == 403:
            raise StartupError(
                f"the dsh browser-trust fence still rejects Host {tunnel_host}; "
                "the public URL would answer 403, check --trusted-host support in this dsh build"
            )
        LOGGER.debug("trust-fence probe for %s returned %s", tunnel_host, status)

        if not wait_until_reachable(url, args.public_timeout, shutdown):
            LOGGER.warning(
                "the public URL did not answer within %gs; the quick tunnel may still be propagating, "
                "retry the link or the QR code in a moment",
                args.public_timeout,
            )

        print_banner(origin, local_url, url, args)
        LOGGER.info("serving the dsh Web UI through the tunnel; press Ctrl-C to stop")
        return monitor(group, shutdown)
    finally:
        if shim is not None:
            shim.stop()
        group.stop_all()
        if remove_runtime_dir and runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    configure_logging(args.log_level)
    shutdown = Shutdown()
    shutdown.install()
    try:
        return run(args, shutdown)
    except KeyboardInterrupt:
        LOGGER.info("interrupted; stopped the tunnel and the dsh Web UI")
        return 130
    except ShutdownRequested as stop:
        LOGGER.info("%s; stopped the tunnel and the dsh Web UI", stop)
        return 128 + stop.signum
    except StartupError as error:
        LOGGER.error("%s", error)
        return 1
