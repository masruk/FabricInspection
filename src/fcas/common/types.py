"""Shared value types.

The vocabulary here is fixed by ui-context.md and is used identically in REST
responses, CLI output, logs, and telemetry. Never invent another member.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self


class _StrictStrEnum(StrEnum):
    """A StrEnum whose parsing is case-sensitive and rejects anything unknown."""

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Parse an exact member name. Case-sensitive by design.

        Accepting ``left`` for ``LEFT`` would let a typo in the config file silently
        map a camera to the wrong side of the web, which is undetectable downstream.
        """
        try:
            return cls(raw)
        except ValueError:
            allowed = ", ".join(m.value for m in cls)
            raise ValueError(f"expected one of [{allowed}], got {raw!r}") from None


class CameraPosition(_StrictStrEnum):
    """Stable logical position across the web. Identity is never by USB port order."""

    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"
    #: Discovered camera whose serial is not in configuration (FR-105). Internal
    #: sentinel only - never a valid configured position.
    UNKNOWN = "UNKNOWN"

    @classmethod
    def configurable(cls) -> tuple[CameraPosition, ...]:
        return (cls.LEFT, cls.CENTER, cls.RIGHT)


class ServiceState(_StrictStrEnum):
    """The state machine of SRS 6.1."""

    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"


class TriggerKind(_StrictStrEnum):
    """How exposures are initiated. Stamped on every published message."""

    HARDWARE = "HARDWARE"
    SOFTWARE = "SOFTWARE"
    FREERUN = "FREERUN"


class LogLevel(_StrictStrEnum):
    """Configurable log level (FR-704). ``WARN`` is the spelling used in the log
    format; the stdlib calls it ``WARNING``."""

    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"
    DEBUG = "DEBUG"


class BayerQuality(_StrictStrEnum):
    """Bayer interpolation quality (FR-402), mapped to MV_CC_SetBayerCvtQuality."""

    BASIC = "BASIC"
    BALANCED = "BALANCED"
    OPTIMAL = "OPTIMAL"


class TriggerActivation(_StrictStrEnum):
    """Trigger edge (FR-204). Values match the GenICam TriggerActivation node."""

    RISING_EDGE = "RisingEdge"
    FALLING_EDGE = "FallingEdge"
