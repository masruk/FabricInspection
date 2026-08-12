"""Path resolution (Unit 01).

These tests exist because a Windows Service starts in ``%SystemRoot%\\System32``. If
resolution ever silently starts depending on the working directory, the service finds
no config and writes logs into System32 - and it only shows up under the SCM, which
is the most expensive place to discover it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcas.common import paths


def test_relative_path_ignores_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(paths.FCAS_HOME_ENV, raising=False)
    expected = paths.resolve("config/fcas.config.json")

    monkeypatch.chdir(tmp_path)

    assert paths.resolve("config/fcas.config.json") == expected
    assert Path.cwd().resolve() != expected.parent


def test_absolute_path_passes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.FCAS_HOME_ENV, raising=False)
    absolute = (tmp_path / "elsewhere" / "fcas.config.json").resolve()

    assert paths.resolve(absolute) == absolute


def test_fcas_home_overrides_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.FCAS_HOME_ENV, str(tmp_path))

    assert paths.install_root() == tmp_path.resolve()
    assert paths.resolve("logs") == (tmp_path / "logs").resolve()


def test_install_root_is_the_repo_root_in_a_src_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(paths.FCAS_HOME_ENV, raising=False)
    root = paths.install_root()

    assert (root / "src" / "fcas").is_dir(), f"unexpected install root {root}"
    assert (root / "pyproject.toml").is_file()


def test_default_config_path_points_at_the_shipped_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(paths.FCAS_HOME_ENV, raising=False)

    assert paths.default_config_path().is_file()
    assert paths.default_config_path().name == "fcas.config.json"


def test_log_path_resolves_the_same_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.FCAS_HOME_ENV, raising=False)
    expected = paths.resolve("logs")

    monkeypatch.chdir(tmp_path)

    assert paths.resolve("logs") == expected
