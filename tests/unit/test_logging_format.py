"""The log line format is a contract (ui-context.md), so it is tested like one."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from fcas.common.types import LogLevel
from fcas.telemetry import logging_setup

#: 2026-08-02 14:33:07.412 [INFO ] [camera ] message
LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \[(\w{1,5}) {0,4}\] \[(.{7})\] (.*)$"
)


def _format(name: str, level: int, message: str) -> str:
    record = logging.LogRecord(name, level, __file__, 1, message, None, None)
    return logging_setup.FcasFormatter().format(record)


def test_line_shape() -> None:
    line = _format("fcas.camera.device", logging.INFO, "LEFT DB0717739 connected")

    match = LINE.match(line)
    assert match is not None, line
    assert match.group(3) == "LEFT DB0717739 connected"


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO "),
        (logging.WARNING, "WARN "),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "FATAL"),
    ],
)
def test_level_is_padded_to_five(level: int, expected: str) -> None:
    line = _format("fcas.service.app", level, "x")

    assert f"[{expected}]" in line, line
    assert len(expected) == 5


@pytest.mark.parametrize(
    ("logger", "tag"),
    [
        ("fcas.service.app", "service"),
        ("fcas.config.loader", "config "),
        ("fcas.camera.device", "camera "),
        ("fcas.pipeline.correlator", "pipelin"),
        ("fcas.publish.publisher", "publish"),
        ("fcas.control.rest_server", "control"),
        ("fcas.telemetry.health", "health "),
    ],
)
def test_component_tag_derived_from_package(logger: str, tag: str) -> None:
    """Every tag in ui-context.md must come out of the logger name alone - call sites
    never pass one, so they cannot get it wrong."""
    assert logging_setup.component_tag(logger) == tag
    assert len(logging_setup.component_tag(logger)) == 7


def test_unknown_package_is_truncated_and_padded() -> None:
    assert logging_setup.component_tag("fcas.somethingnew.mod") == "somethi"
    assert logging_setup.component_tag("fcas.ab.mod") == "ab     "


def test_message_formatting_is_lazy() -> None:
    """Call sites use %s args; the formatter must apply them."""
    record = logging.LogRecord(
        "fcas.publish.p",
        logging.WARNING,
        __file__,
        1,
        "drop reason=%s sequence=%d",
        ("BROKER_UNAVAILABLE", 88215),
        None,
    )

    assert "drop reason=BROKER_UNAVAILABLE sequence=88215" in logging_setup.FcasFormatter().format(
        record
    )


def test_console_handler_only_in_console_mode(tmp_path: Path) -> None:
    """Under the SCM there is no stdout; a console handler there is a defect (Unit 09)."""
    logging_setup.setup_logging(
        level=LogLevel.INFO, log_dir=tmp_path, max_bytes=1024, backup_count=1, console=False
    )
    logger = logging.getLogger(logging_setup.ROOT_LOGGER)
    assert not any(type(h) is logging.StreamHandler for h in logger.handlers)

    logging_setup.setup_logging(
        level=LogLevel.INFO, log_dir=tmp_path, max_bytes=1024, backup_count=1, console=True
    )
    assert any(type(h) is logging.StreamHandler for h in logger.handlers)

    logging_setup.shutdown_logging()


def test_setup_is_idempotent(tmp_path: Path) -> None:
    for _ in range(3):
        logging_setup.setup_logging(
            level=LogLevel.DEBUG, log_dir=tmp_path, max_bytes=1024, backup_count=1, console=True
        )

    logger = logging.getLogger(logging_setup.ROOT_LOGGER)
    assert len(logger.handlers) == 2, "handlers must be replaced, not accumulated"

    logging_setup.shutdown_logging()


def test_file_is_written_and_rotates(tmp_path: Path) -> None:
    logging_setup.setup_logging(
        level=LogLevel.INFO, log_dir=tmp_path, max_bytes=512, backup_count=2, console=False
    )
    log = logging.getLogger("fcas.service.app")
    for i in range(200):
        log.info("padding line %03d %s", i, "x" * 40)
    logging_setup.shutdown_logging()

    assert (tmp_path / "fcas.log").is_file()
    assert (tmp_path / "fcas.log.1").is_file(), "rotation did not occur at the configured size"
