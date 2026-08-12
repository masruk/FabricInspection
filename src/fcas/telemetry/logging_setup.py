"""Logging configuration.

The line format is fixed by ui-context.md and owned entirely by :class:`FcasFormatter`.
Call sites use ``logging.getLogger(__name__)`` and lazy ``%s`` formatting; they never
build a line themselves and never pass a component tag - the tag is derived from the
logger's package so it cannot drift.

::

    2026-08-02 14:33:07.412 [INFO ] [camera ] LEFT DB0717739 connected
    2026-08-02 14:33:07.598 [INFO ] [service] state IDLE -> READY (cameras opened: 3)
    2026-08-02 14:33:12.004 [WARN ] [publish] drop reason=BROKER_UNAVAILABLE ...

Timestamp with milliseconds, level padded to five, component tag padded to seven.
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

from fcas.common.types import LogLevel

ROOT_LOGGER = "fcas"

_LEVEL_WIDTH = 5
_TAG_WIDTH = 7

#: stdlib level name -> the five-character spelling used in our log format.
_LEVEL_NAMES = {
    "WARNING": "WARN",
    "CRITICAL": "FATAL",
}

#: Package under ``fcas.`` -> component tag. Anything unlisted is truncated/padded,
#: which is why ``pipeline`` needs no entry (it truncates to ``pipelin`` correctly)
#: but ``telemetry`` does (it would truncate to ``telemet``).
_TAGS = {
    "telemetry": "health",
    "fcasctl": "cli",
    "common": "service",
    "__main__": "service",
}

#: Config level -> stdlib level. ``WARN`` is our spelling; the stdlib says ``WARNING``.
_LEVELS = {
    LogLevel.ERROR: logging.ERROR,
    LogLevel.WARN: logging.WARNING,
    LogLevel.INFO: logging.INFO,
    LogLevel.DEBUG: logging.DEBUG,
}


def component_tag(logger_name: str) -> str:
    """Derive the seven-character component tag from a logger name.

    ``fcas.camera.device`` -> ``camera ``; ``fcas.pipeline.correlator`` -> ``pipelin``.
    """
    parts = logger_name.split(".")
    package = parts[1] if len(parts) > 1 and parts[0] == ROOT_LOGGER else parts[0]
    return _TAGS.get(package, package)[:_TAG_WIDTH].ljust(_TAG_WIDTH)


class FcasFormatter(logging.Formatter):
    """The single owner of the log line format."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
        level = _LEVEL_NAMES.get(record.levelname, record.levelname)[:_LEVEL_WIDTH]
        line = (
            f"{stamp}.{int(record.msecs):03d} "
            f"[{level.ljust(_LEVEL_WIDTH)}] "
            f"[{component_tag(record.name)}] "
            f"{record.getMessage()}"
        )
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def setup_logging(
    *,
    level: LogLevel,
    log_dir: Path,
    max_bytes: int,
    backup_count: int,
    console: bool,
) -> None:
    """Configure the ``fcas`` logger tree. Idempotent - safe to call twice.

    The console handler is attached **only** when running in console mode. Under the
    SCM there is no stdout, so a console handler there is at best useless and at
    worst an exception on a closed handle (Unit 09 depends on this switch).
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(_LEVELS[level])
    # Ours is the whole tree; letting records reach the root logger would double them
    # through whatever a dependency has configured.
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    formatter = FcasFormatter()

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "fcas.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)


def shutdown_logging() -> None:
    """Flush and detach handlers. Called on the teardown path (OP-104)."""
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        handler.flush()
        logger.removeHandler(handler)
        handler.close()
