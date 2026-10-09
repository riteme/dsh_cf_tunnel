"""Startup preflight: locate the external tools this program drives.

`cloudflared` and the `dsh` CLI are both required before anything is started, and
this module is the only place that decides whether they are there. Nothing here
installs, upgrades, or downloads a tool: a missing one is reported with the
commands the user should run, and the program exits. The single network call is
the GitHub release lookup, which only names the latest cloudflared version in that
report.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import NamedTuple, NoReturn

from .log import LOGGER

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


# The published CLI. Verified against the npm registry rather than assumed: the
# PyPI name `deepseek-harness` belongs to an unrelated project and must never be
# suggested as an install route.
DSH_NPM_PACKAGE = "@deepseek-ai/dsh"


DSH_REPOSITORY = "https://github.com/deepseek-ai/deepseek-harness"


# A missing tool is a usage problem, so the exit status is a plain failure.
EXIT_MISSING_TOOL = 1


VERSION_TIMEOUT_SECONDS = 30.0


# Printed once under every missing-tool report, so the user knows the program will
# not do the installation for them.
INSTALL_FOOTER = "dsh-cf-tunnel only launches these tools; it does not install or upgrade them."


class Tools(NamedTuple):
    """Resolved executables the startup sequence needs before it starts anything."""

    cloudflared: str
    dsh: str


def resolve_executable(binary: str) -> str | None:
    """Resolve a configured executable, honoring explicit paths before PATH lookups."""
    if os.sep in binary:
        return binary if os.access(binary, os.X_OK) else None
    return shutil.which(binary)


def executable_version(binary: str) -> str | None:
    """First line of ``<binary> --version``, or None when the tool cannot answer.

    This is only used for the startup log line, so a tool that does not answer is
    not a failure: the child's own output will explain what is wrong with it.
    """
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        LOGGER.debug("could not read the version of %s: %s", binary, error)
        return None
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not output:
        LOGGER.warning("%s --version answered with code %s; continuing anyway", binary, completed.returncode)
        return None
    return output.splitlines()[0]


def describe_tool(binary: str) -> str:
    """``<version> (<path>)`` for a resolved tool, so the log names the install in use."""
    version = executable_version(binary)
    return f"{version} ({binary})" if version else binary


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


def dsh_install_help(dsh_bin: str) -> str:
    """Build the install instructions printed when the dsh CLI is missing.

    The npm package is the published route; a checkout or a desktop installation is
    a path the user points ``--dsh-bin`` at rather than something to install here.
    """
    return "\n".join(
        [
            f"dsh: '{dsh_bin}' was not found in PATH.",
            "dsh-cf-tunnel serves the dsh Web UI, so the dsh CLI has to be installed already.",
            "",
            "  1) Install the published CLI with npm:",
            f"       npm install -g {DSH_NPM_PACKAGE}",
            "",
            "  2) Or use an existing installation:",
            f"       the DeepSeek Harness source tree: {DSH_REPOSITORY}",
            "       a dsh bundled with the desktop app, or one installed for another Node",
            "       version, is reached with --dsh-bin /path/to/dsh",
            "",
            "If npm installed dsh but PATH does not see it, run 'npm prefix -g' and add that",
            "installation's bin directory to PATH.",
        ]
    )


def _report_missing(helps: Sequence[str]) -> NoReturn:
    """Print every missing-tool report and exit, without touching the user's system."""
    print("\n\n".join([*helps, INSTALL_FOOTER]), file=sys.stderr)
    raise SystemExit(EXIT_MISSING_TOOL)


def ensure_executable(binary: str, install_help: Callable[[str], str]) -> str:
    """Resolve one external tool, or exit after printing how to install it."""
    resolved = resolve_executable(binary)
    if resolved is None:
        _report_missing([install_help(binary)])
    return resolved


def ensure_cloudflared(binary: str) -> str:
    """Return the cloudflared path, or exit after printing install instructions."""
    return ensure_executable(binary, cloudflared_install_help)


def ensure_dsh(binary: str) -> str:
    """Return the dsh path, or exit after printing install instructions."""
    return ensure_executable(binary, dsh_install_help)


def ensure_tools(cloudflared_bin: str, dsh_bin: str) -> Tools:
    """Resolve both external tools, reporting every missing one in a single exit.

    @returns the resolved paths, so no caller can start a child process for a tool
    that was never found.
    """
    missing = []
    cloudflared = resolve_executable(cloudflared_bin)
    if cloudflared is None:
        missing.append(cloudflared_install_help(cloudflared_bin))
    dsh = resolve_executable(dsh_bin)
    if dsh is None:
        missing.append(dsh_install_help(dsh_bin))
    if missing:
        _report_missing(missing)
    return Tools(cloudflared=cloudflared, dsh=dsh)
