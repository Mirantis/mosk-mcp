"""Allow ``python -m mosk_mcp`` (delegates to :mod:`mosk_mcp.cli`)."""

from __future__ import annotations

from mosk_mcp.cli import main

if __name__ == "__main__":
    main()
