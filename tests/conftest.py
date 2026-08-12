"""Shared fixtures.

The buffer-pool leak fixture that every later pipeline test depends on is added in
Unit 03, once there is a pool to assert on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = REPO_ROOT / "config" / "fcas.config.json"


@pytest.fixture
def base_config() -> dict[str, Any]:
    """The shipped default configuration, as a mutable dict.

    Tests start from the file that actually ships, so a change to the default that
    breaks validation fails a test rather than only the next person to clone.
    """
    loaded: dict[str, Any] = json.loads(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    return loaded


@pytest.fixture
def write_config(tmp_path: Path) -> Iterator[Any]:
    """Write a config dict to a temp file and return its path."""

    def _write(document: dict[str, Any], name: str = "fcas.config.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path

    yield _write
