"""Serve ``dsh --profile web`` behind a Cloudflare quick tunnel, with a scannable QR code.

The command line entry point is :func:`dsh_cf_tunnel.cli.main`; the other modules
own one concern each:

``cli``
    Flags, startup order, banner, and process supervision.
``cloudflared``
    The cloudflared preflight, install guidance, and the quick-tunnel launcher.
``shim``
    The loopback auth shim that keeps dsh's session cookie off the client's plate.
``hostpage``
    The generated ``dsh --patch`` overlay that makes the tunneled page host-owned.
``qr``
    Terminal and PNG QR rendering (segno is the only third-party dependency).
``process``, ``log``, ``errors``
    Child-process ownership, logging, and this package's own failures.
"""

from __future__ import annotations

from .cli import main
from .errors import ShutdownRequested, StartupError

__version__ = "0.1.0"
__all__ = ["ShutdownRequested", "StartupError", "__version__", "main"]
