"""Configuration loading and validation (Unit 01)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Protocol

import pytest

from fcas.common.errors import ConfigError, ErrorCode
from fcas.common.types import BayerQuality, CameraPosition, LogLevel
from fcas.config import loader


class WriteConfig(Protocol):
    def __call__(self, document: dict[str, Any], name: str = ...) -> Path: ...


# --- happy path -----------------------------------------------------------


def test_shipped_config_loads(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    loaded = loader.load(write_config(base_config))

    assert len(loaded.resolved_cameras) == 3
    assert [c.position for c in loaded.resolved_cameras] == [
        CameraPosition.LEFT,
        CameraPosition.CENTER,
        CameraPosition.RIGHT,
    ]
    assert loaded.config.service.log_level is LogLevel.INFO


def test_defaults_merge_and_entry_overrides(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    base_config["cameraDefaults"]["exposureUs"] = 500
    base_config["cameraDefaults"]["gainDb"] = 3.0
    base_config["cameras"][0]["exposureUs"] = 650  # override
    base_config["cameras"][1].pop("exposureUs", None)  # inherit

    loaded = loader.load(write_config(base_config))
    left, center, _ = loaded.resolved_cameras

    assert left.settings.exposure_us == 650, "per-camera value must win"
    assert center.settings.exposure_us == 500, "absent value must inherit the default"
    assert left.settings.gain_db == 3.0, "unrelated defaults still apply"
    assert left.settings.bayer_quality is BayerQuality.BALANCED


def test_routing_key_defaults_from_position(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    for entry in base_config["cameras"]:
        entry.pop("routingKey", None)

    loaded = loader.load(write_config(base_config))

    assert [c.routing_key for c in loaded.resolved_cameras] == [
        "camera.left",
        "camera.center",
        "camera.right",
    ]


def test_frame_bytes_matches_the_contract(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    loaded = loader.load(write_config(base_config))
    # 2448 x 2048 x 3 = 15 040 512 bytes, the figure the memory budget is built on.
    assert loaded.resolved_cameras[0].settings.frame_bytes == 15_040_512


# --- rejection ------------------------------------------------------------


def _issues_from(path: Path) -> tuple[str, ...]:
    with pytest.raises(ConfigError) as caught:
        loader.load(path)
    assert caught.value.code is ErrorCode.E_CFG_INVALID_VALUE
    return caught.value.issues


def test_duplicate_serial_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    base_config["cameras"][1]["serial"] = base_config["cameras"][0]["serial"]

    issues = _issues_from(write_config(base_config))

    assert any("duplicate" in i and "serial" in i for i in issues), issues


def test_duplicate_position_rejected(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    base_config["cameras"][1]["position"] = "LEFT"

    issues = _issues_from(write_config(base_config))

    assert any("duplicate" in i and "position" in i for i in issues), issues


def test_unknown_position_is_not_assignable(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    """UNKNOWN is the internal sentinel for an unmapped camera (FR-105). Accepting it
    from config would let an operator configure a camera into a state the pipeline
    treats as 'not ours', which would silently exclude it from acquisition."""
    base_config["cameras"][0]["position"] = "UNKNOWN"

    issues = _issues_from(write_config(base_config))

    assert any("cameras[0].position" in i and "not assignable" in i for i in issues), issues


@pytest.mark.parametrize("value", ["left", "Left", "MIDDLE", ""])
def test_invalid_position_rejected(
    write_config: WriteConfig, base_config: dict[str, Any], value: str
) -> None:
    """Position parsing is case-sensitive: 'left' mapping to LEFT would silently put a
    camera on the wrong side of the web, which nothing downstream can detect."""
    base_config["cameras"][0]["position"] = value

    issues = _issues_from(write_config(base_config))

    assert any("cameras[0].position" in i for i in issues), issues


@pytest.mark.parametrize(("port", "field"), [(0, "restPort"), (65536, "restPort")])
def test_out_of_range_rest_port_rejected(
    write_config: WriteConfig, base_config: dict[str, Any], port: int, field: str
) -> None:
    base_config["service"]["restPort"] = port

    issues = _issues_from(write_config(base_config))

    assert any(f"service.{field}" in i for i in issues), issues


def test_out_of_range_broker_port_rejected(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    base_config["rabbitmq"]["port"] = 70000

    issues = _issues_from(write_config(base_config))

    assert any("rabbitmq.port" in i for i in issues), issues


def test_invalid_log_level_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    base_config["service"]["logLevel"] = "VERBOSE"

    issues = _issues_from(write_config(base_config))

    assert any("service.logLevel" in i for i in issues), issues


def test_invalid_trigger_kind_rejected(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    base_config["acquisition"]["triggerKind"] = "CONTINUOUS"

    issues = _issues_from(write_config(base_config))

    assert any("acquisition.triggerKind" in i for i in issues), issues


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("rabbitmq", "queueMaxLength", 0),
        ("rabbitmq", "messageTtlMs", 0),
        ("acquisition", "localQueueDepth", 0),
        ("acquisition", "groupingWindowMs", 0),
        ("acquisition", "bufferPoolSize", 0),
    ],
)
def test_positive_integers_enforced(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    section: str,
    field: str,
    value: int,
) -> None:
    base_config[section][field] = value

    issues = _issues_from(write_config(base_config))

    assert any(f"{section}.{field}" in i for i in issues), issues


@pytest.mark.parametrize(("field", "value"), [("width", 0), ("height", 0), ("offsetX", -1)])
def test_geometry_bounds_enforced(
    write_config: WriteConfig, base_config: dict[str, Any], field: str, value: int
) -> None:
    base_config["cameraDefaults"][field] = value

    issues = _issues_from(write_config(base_config))

    assert any(f"cameraDefaults.{field}" in i for i in issues), issues


def test_negative_gain_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    base_config["cameras"][2]["gainDb"] = -1.0

    issues = _issues_from(write_config(base_config))

    assert any("cameras[2].gainDb" in i for i in issues), issues


def test_empty_camera_list_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    base_config["cameras"] = []

    issues = _issues_from(write_config(base_config))

    assert any("cameras" in i for i in issues), issues


def test_unknown_key_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    """A typo'd key must be an error, not a setting that silently does nothing."""
    base_config["service"]["restPortt"] = 8080

    issues = _issues_from(write_config(base_config))

    assert any("restPortt" in i for i in issues), issues


# --- the important one ----------------------------------------------------


def test_all_errors_reported_in_one_run(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    """A duplicate serial and an invalid position must BOTH appear in one report.

    These come from two different validation passes - pydantic field validation and
    the raw structural scan - so this is the test that would fail if the duplicate
    scan were folded into a model_validator, which never runs once a field has failed.
    """
    base_config["cameras"][1]["serial"] = base_config["cameras"][0]["serial"]
    base_config["cameras"][2]["position"] = "middle"
    base_config["service"]["restPort"] = 0

    issues = _issues_from(write_config(base_config))

    assert any("duplicate" in i for i in issues), issues
    assert any("cameras[2].position" in i for i in issues), issues
    assert any("service.restPort" in i for i in issues), issues
    assert len(issues) >= 3


# --- file-level failures --------------------------------------------------


def test_missing_file_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        loader.load(tmp_path / "absent.json")
    assert caught.value.code is ErrorCode.E_CFG_FILE_NOT_FOUND


def test_malformed_json_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        loader.load(path)
    assert caught.value.code is ErrorCode.E_CFG_PARSE_FAILED
    assert "line" in caught.value.message


def test_non_object_document_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        loader.load(path)
    assert caught.value.code is ErrorCode.E_CFG_PARSE_FAILED


# --- warnings, not errors -------------------------------------------------


def test_exposure_above_ceiling_warns_but_loads(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    """FR-206: above the ceiling is a warning. The device layer clamps at apply time."""
    base_config["acquisition"]["exposureCeilingUs"] = 800
    base_config["cameras"][0]["exposureUs"] = 1500

    loaded = loader.load(write_config(base_config))

    assert loaded.resolved_cameras[0].settings.exposure_us == 1500
    assert any("exceeds" in w and "LEFT" in w for w in loaded.warnings), loaded.warnings


# --- credentials ----------------------------------------------------------


def test_env_reference_resolves(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FCAS_TEST_MQ_PASSWORD", "s3cret-value")
    base_config["rabbitmq"]["passwordRef"] = "env:FCAS_TEST_MQ_PASSWORD"

    loaded = loader.load(write_config(base_config))

    assert loaded.broker_password is not None
    assert loaded.broker_password.get_secret_value() == "s3cret-value"


def test_unset_env_reference_warns_but_loads(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FCAS_ABSENT_PASSWORD", raising=False)
    base_config["rabbitmq"]["passwordRef"] = "env:FCAS_ABSENT_PASSWORD"

    loaded = loader.load(write_config(base_config))

    assert loaded.broker_password is None
    assert any("FCAS_ABSENT_PASSWORD" in w for w in loaded.warnings)


def test_literal_password_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    """A password in the file is a security defect, not a convenience (NFR-401)."""
    base_config["rabbitmq"]["passwordRef"] = "hunter2"

    with pytest.raises(ConfigError) as caught:
        loader.load(write_config(base_config))
    assert caught.value.code is ErrorCode.E_CFG_SECRET_UNRESOLVED


def test_credential_never_appears_in_summary_or_logs(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "do-not-log-me-9f3a"
    monkeypatch.setenv("FCAS_TEST_MQ_PASSWORD", secret)
    base_config["rabbitmq"]["passwordRef"] = "env:FCAS_TEST_MQ_PASSWORD"

    with caplog.at_level(logging.DEBUG):
        loaded = loader.load(write_config(base_config))
        summary = loader.summarise(loaded)

    assert secret not in "\n".join(summary)
    assert secret not in caplog.text
    # pydantic's SecretStr must also redact under repr, which is what reaches a
    # traceback or a careless log call.
    assert secret not in repr(loaded)
    assert secret not in str(loaded.broker_password)
    assert "password=<set>" in "\n".join(summary)


def test_summary_names_every_camera(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    summary = "\n".join(loader.summarise(loader.load(write_config(base_config))))

    for entry in base_config["cameras"]:
        assert entry["serial"] in summary
        assert entry["position"] in summary


def test_shipped_config_file_is_valid() -> None:
    """The committed default must load. Guards against editing it into an invalid state."""
    from tests.conftest import SHIPPED_CONFIG

    loaded = loader.load(SHIPPED_CONFIG)
    assert json.loads(SHIPPED_CONFIG.read_text(encoding="utf-8"))["cameras"][0]["serial"] == (
        loaded.resolved_cameras[0].serial
    )
