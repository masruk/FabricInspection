"""Error taxonomy for FCAS.

Errors are exceptions internally and a result envelope at the boundaries (SDD 9).
Every error preserves the raw vendor return code it came from - the SDK hex value or
the AMQP reply code - because field diagnosis needs to reference vendor documentation
directly. Never discard a vendor code.
"""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    """Numeric FCAS error codes, namespaced by domain.

    The blocks are reserved up front so later units slot in without renumbering:

    ==========  ========  ======================================
    Range       Prefix    Domain
    ==========  ========  ======================================
    0           -         success
    1000-1099   E_CFG_    configuration (fatal - refuse to start)
    1100-1199   E_SVC_    service lifecycle
    1200-1299   E_CAM_    SDK / device            (Unit 02+)
    1300-1399   E_ACQ_    acquisition             (Unit 04+)
    1400-1499   E_COR_    correlation             (Unit 05+)
    1500-1599   E_MQ_     broker / publish        (Unit 06+)
    ==========  ========  ======================================
    """

    OK = 0

    # --- E_CFG_* - configuration. Always fatal; FCAS refuses to start (FR-210).
    E_CFG_FILE_NOT_FOUND = 1001
    E_CFG_PARSE_FAILED = 1002
    E_CFG_INVALID_VALUE = 1003
    E_CFG_SECRET_UNRESOLVED = 1004

    # --- E_SVC_* - service lifecycle.
    E_SVC_ALREADY_STARTED = 1101
    E_SVC_NOT_STARTED = 1102
    E_SVC_START_FAILED = 1103
    E_SVC_SHUTDOWN_TIMEOUT = 1104
    E_SVC_NOT_IMPLEMENTED = 1105
    E_SVC_INTERNAL = 1106


class FcasError(Exception):
    """Base for every FCAS error.

    Deliberately a plain exception rather than a dataclass: a
    ``@dataclass(frozen=True, slots=True)`` exception renders as ``(1003, 'msg')``
    under ``str()``, which is what would reach the log file. Logs are the primary
    diagnostic on an unattended box, so the message has to survive formatting.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        sdk_ret: int | None = None,
        amqp_ret: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.sdk_ret = sdk_ret
        self.amqp_ret = amqp_ret

    def __str__(self) -> str:
        parts = [f"[{self.code.name}] {self.message}"]
        if self.sdk_ret is not None:
            parts.append(f"sdk=0x{self.sdk_ret & 0xFFFFFFFF:08x}")
        if self.amqp_ret is not None:
            parts.append(f"amqp={self.amqp_ret}")
        return " ".join(parts)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code.name}, {self.message!r})"


class ConfigError(FcasError):
    """Configuration is missing, unparseable, or invalid. Always fatal (FR-210).

    ``issues`` carries every problem found in one pass, each naming its field path,
    so an operator fixes the whole file in one edit rather than one field per restart.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        issues: tuple[str, ...] = (),
    ) -> None:
        super().__init__(code, message)
        self.issues = issues


class ServiceError(FcasError):
    """Service lifecycle failure."""
