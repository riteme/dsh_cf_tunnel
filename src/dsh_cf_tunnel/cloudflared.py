"""cloudflared preflight, install guidance, and the quick-tunnel launcher."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import urllib.error
import urllib.request

from .log import LOGGER
from .process import ProcessGroup, Shutdown

# The stable "latest release" asset URL always resolves to the newest build, so
# the download command never goes stale; the API call is only used to report and
# display the concrete version number.
CLOUDFLARED_RELEASES_API = "https://api.github.com/repos/cloudflare/cloudflared/releases/latest"


CLOUDFLARED_DOWNLOAD_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-{system}-{arch}"
)


CLOUDFLARED_ARCHES = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
    "i686": "386",
    "i386": "386",
}


CLOUDFLARED_APT_SOURCE = (
    "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main"
)


# A failed quick-tunnel request may still be retried by cloudflared, so the
# failure is only fatal once no tunnel URL followed within this grace period.
QUICK_TUNNEL_FATAL_GRACE_SECONDS = 20.0


# cloudflared prints the quick-tunnel hostname in a box, but error lines embed the
# API host (`Post "https://api.trycloudflare.com/tunnel": ...`), which must never be
# mistaken for a tunnel: reserved labels are excluded and failures are reported.
QUICK_TUNNEL_URL_PATTERN = re.compile(
    r"https://(?!(?:api|www|developers|blog|community|support|dash|one|update)\.)[a-z0-9][a-z0-9-]*\.trycloudflare\.com"
)


QUICK_TUNNEL_FAILURE_PATTERN = re.compile(r"failed to (?:request|create) (?:quick )?[Tt]unnel", re.IGNORECASE)


def resolve_executable(binary: str) -> str | None:
    """Resolve a configured executable, honoring explicit paths before PATH lookups."""
    if os.sep in binary:
        return binary if os.access(binary, os.X_OK) else None
    return shutil.which(binary)


def cloudflared_arch() -> str | None:
    """Map the running machine onto the cloudflared release asset suffix."""
    return CLOUDFLARED_ARCHES.get(platform.machine().lower())


def latest_cloudflared_version(timeout: float = 8.0) -> str | None:
    """Query the newest published cloudflared tag, or None when the API is unreachable."""
    request = urllib.request.Request(
        CLOUDFLARED_RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dsh-cf-tunnel"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as error:
        LOGGER.debug("latest cloudflared version unavailable: %s", error)
        return None
    tag = payload.get("tag_name")
    return tag if isinstance(tag, str) else None


def cloudflared_install_help(cloudflared_bin: str) -> str:
    """Build the install instructions printed when cloudflared is missing."""
    system = platform.system().lower()
    arch = cloudflared_arch()
    machine = platform.machine()
    version = latest_cloudflared_version() if system == "linux" else None
    version_text = f" (latest release: {version})" if version else ""

    lines = [
        f"cloudflared: '{cloudflared_bin}' was not found in PATH.",
        f"Install cloudflared{version_text} with one of the following:",
        "",
        "  1) Debian/Ubuntu package repository (kept up to date by apt):",
        "       sudo mkdir -p --mode=0755 /usr/share/keyrings",
        "       curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \\",
        "         | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null",
        f"       echo '{CLOUDFLARED_APT_SOURCE}' \\",
        "         | sudo tee /etc/apt/sources.list.d/cloudflared.list",
        "       sudo apt-get update && sudo apt-get install -y cloudflared",
        "",
    ]
    if system == "linux" and arch is not None:
        lines += [
            "  2) Standalone binary from the latest GitHub release (no root needed):",
            f"       mkdir -p ~/.local/bin && curl -fL {CLOUDFLARED_DOWNLOAD_URL.format(system='linux', arch=arch)} \\",
            "         -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared",
            "",
            f"  Detected platform: linux/{machine} -> cloudflared-linux-{arch}",
        ]
    else:
        lines += [
            "  2) Standalone binary, see https://github.com/cloudflare/cloudflared/releases/latest",
            f"     (detected platform {system}/{machine} is not mapped to a release asset here)",
        ]
    lines += [
        "",
        "If cloudflared is installed outside PATH, pass --cloudflared-bin /path/to/cloudflared.",
        "Behind a proxy, export HTTPS_PROXY/HTTP_PROXY before downloading.",
    ]
    return "\n".join(lines)


def ensure_cloudflared(binary: str) -> str:
    """Return the cloudflared path, or exit after printing install instructions."""
    resolved = resolve_executable(binary)
    if resolved is not None:
        return resolved
    print(cloudflared_install_help(binary), file=sys.stderr)
    raise SystemExit(1)


def start_cloudflared(
    group: ProcessGroup,
    binary: str,
    port: int,
    timeout: float,
    shutdown: Shutdown,
) -> str:
    """Start the quick tunnel and return its public origin."""
    output = group.spawn(
        [
            binary,
            "tunnel",
            "--no-autoupdate",
            "--loglevel",
            "info",
            "--url",
            f"http://127.0.0.1:{port}",
        ],
        name="cloudflared",
    )
    match = output.wait_for(
        QUICK_TUNNEL_URL_PATTERN,
        timeout,
        "create the quick tunnel",
        shutdown,
        fatal=QUICK_TUNNEL_FAILURE_PATTERN,
        fatal_grace=QUICK_TUNNEL_FATAL_GRACE_SECONDS,
    )
    origin = match.group(0)
    LOGGER.info("quick tunnel ready: %s", origin)
    return origin
