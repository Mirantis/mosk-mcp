"""Single source of truth for the mosk-mcp distribution version.

``pyproject.toml`` uses Hatch to read ``__version__`` from this file (see ``[tool.hatch.version]``).
Runtime code should import ``__version__`` from ``mosk_mcp`` or from this module.
"""

__version__ = "0.1.0"
