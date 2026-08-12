"""CLI surface and exit codes (Unit 01).

Exit codes are the contract with whatever supervises this process: 0 clean,
1 runtime failure, 2 usage error, 3 configuration invalid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

import pytest

from fcas.__main__ import EXIT_CONFIG, EXIT_OK, EXIT_USAGE, main


class WriteConfig(Protocol):
    def __call__(self, document: dict[str, Any], name: str = ...) -> Path: ...


def test_no_arguments_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_USAGE
    assert capsys.readouterr().err.startswith("usage")


def test_version_exits_clean(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == EXIT_OK

    out = capsys.readouterr().out
    assert out.startswith("fcas ")
    assert "CPython" in out
    assert "64-bit" in out, "a 32-bit interpreter cannot load the 64-bit MVS DLL"


def test_invalid_config_exits_three_and_lists_every_problem(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_config["cameras"][1]["serial"] = base_config["cameras"][0]["serial"]
    base_config["cameras"][2]["position"] = "middle"
    path = write_config(base_config)

    assert main(["run", "--console", "--config", str(path)]) == EXIT_CONFIG

    err = capsys.readouterr().err
    assert "duplicate" in err
    assert "cameras[2].position" in err


def test_missing_config_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "nope.json"

    assert main(["run", "--console", "--config", str(missing)]) == EXIT_CONFIG
    assert "not found" in capsys.readouterr().err


def test_malformed_config_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{oops", encoding="utf-8")

    assert main(["run", "--console", "--config", str(path)]) == EXIT_CONFIG
    assert "not valid JSON" in capsys.readouterr().err


def test_run_without_console_is_rejected_until_unit_09(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run"]) == EXIT_USAGE
    assert "Unit 09" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "unit"),
    [
        (["list-cameras"], "Unit 02"),
        (["capture", "5"], "Unit 03"),
        (["measure-skew", "10"], "Unit 12"),
        (["install"], "Unit 09"),
        (["uninstall"], "Unit 09"),
    ],
)
def test_unimplemented_commands_say_which_unit_builds_them(
    command: list[str], unit: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Declared so the surface is stable from the start, but never silently no-ops."""
    assert main(command) == EXIT_USAGE
    assert unit in capsys.readouterr().err


def test_mock_cameras_is_rejected_until_unit_05(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--console", "--mock-cameras", "2"]) == EXIT_USAGE
    assert "Unit 05" in capsys.readouterr().err


def test_config_error_output_carries_no_credential(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "never-print-me-4b21"
    monkeypatch.setenv("FCAS_TEST_MQ_PASSWORD", secret)
    base_config["rabbitmq"]["passwordRef"] = "env:FCAS_TEST_MQ_PASSWORD"
    base_config["service"]["restPort"] = 0
    path = write_config(base_config)

    assert main(["run", "--console", "--config", str(path)]) == EXIT_CONFIG

    captured = capsys.readouterr()
    assert secret not in captured.err
    assert secret not in captured.out


def test_fcasctl_version(capsys: pytest.CaptureFixture[str]) -> None:
    from fcas.fcasctl.__main__ import main as ctl_main

    assert ctl_main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.startswith("fcasctl ")


def test_fcasctl_no_arguments_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    from fcas.fcasctl.__main__ import main as ctl_main

    assert ctl_main([]) == EXIT_USAGE
    assert "usage" in capsys.readouterr().err


def test_shipped_config_is_json_and_parses() -> None:
    from tests.conftest import SHIPPED_CONFIG

    document = json.loads(SHIPPED_CONFIG.read_text(encoding="utf-8"))
    assert document["cameras"][0]["serial"] == "DB0717739"
    assert document["cameras"][0]["position"] == "LEFT"
