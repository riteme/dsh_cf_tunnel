"""Run the command line entry point as ``python -m dsh_cf_tunnel``."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
