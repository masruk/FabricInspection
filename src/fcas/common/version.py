"""Version reporting.

The version lives in ``pyproject.toml`` and is read back from the installed package
metadata, so there is exactly one place to change it.
"""

from __future__ import annotations

import platform
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

_PACKAGE = "fcas"


def version() -> str:
    """The FCAS version, or a clearly-marked placeholder if not installed."""
    try:
        return _dist_version(_PACKAGE)
    except PackageNotFoundError:
        return "0.0.0+not-installed"


def runtime_summary() -> str:
    """One line identifying the interpreter, logged at startup.

    Worth logging because a 32-bit interpreter cannot load the 64-bit MVS DLL, and
    the resulting error is not obviously about bitness.
    """
    bits = 64 if sys.maxsize > 2**32 else 32
    return (
        f"CPython {platform.python_version()} {bits}-bit "
        f"({sys.executable}) on {platform.system()} {platform.release()}"
    )
