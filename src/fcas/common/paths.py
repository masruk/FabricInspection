"""Path resolution.

A Windows Service starts with a working directory of ``%SystemRoot%\\System32``, not
the directory it was installed into. Every relative path in this project - config,
logs, diagnostics - therefore resolves against the *installation root*, never
``os.getcwd()``. This is the single most common cause of "works in console, fails as
a service", and Unit 09 depends on it having been right from the start.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Escape hatch for deployments that place config outside the detected root.
#: Set it machine-scope alongside the service registration if you need it.
FCAS_HOME_ENV = "FCAS_HOME"


def install_root() -> Path:
    """The directory that ``config/``, ``logs/`` and ``diagnostics/`` hang off.

    Resolution order:

    1. ``$FCAS_HOME`` if set - an explicit deployment override.
    2. The repository root, when running from a ``src/`` layout checkout
       (``<root>/src/fcas/common/paths.py``). This is the development case.
    3. ``sys.prefix`` - the virtual environment root. This is the deployed case,
       where the package lives in ``site-packages`` and config sits beside the venv.
    """
    override = os.environ.get(FCAS_HOME_ENV)
    if override:
        return Path(override).resolve()

    # .../src/fcas/common/paths.py -> parents[0]=common, [1]=fcas, [2]=src
    package_parent = Path(__file__).resolve().parents[2]
    if package_parent.name == "src":
        return package_parent.parent

    return Path(sys.prefix).resolve()


def resolve(path: str | Path) -> Path:
    """Resolve *path* against :func:`install_root` unless it is already absolute."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (install_root() / candidate).resolve()


def default_config_path() -> Path:
    """The config file used when ``--config`` is not given."""
    return resolve(Path("config") / "fcas.config.json")
