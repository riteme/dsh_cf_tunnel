"""The cloudflared quick-tunnel launcher: start the tunnel and read its public origin."""

from __future__ import annotations

import re

from .log import LOGGER
from .process import ProcessGroup, Shutdown

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
