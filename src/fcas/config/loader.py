"""Configuration loading, merging, and validation.

Validation reports **every** problem in one pass, each naming its field path
(``cameras[1].exposureUs``). An operator fixes the whole file in one edit rather
than discovering one more error per restart - which matters when a restart means a
stopped inspection line.

Two independent checks are combined to make that work:

* pydantic field validation, which already collects all field-level errors; and
* a structural scan of the raw document for duplicate serials and positions.

The scan runs on the raw dictionary rather than the parsed model because a
``model_validator`` never runs when field validation has already failed - so a config
with both an invalid position *and* a duplicate serial would otherwise report only
the first. Two passes, one combined report.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import SecretStr, ValidationError

from fcas.common.errors import ConfigError, ErrorCode
from fcas.config.schema import (
    CameraSettings,
    Config,
    LoadedConfig,
    ResolvedCamera,
)

log = logging.getLogger(__name__)

_ENV_PREFIX = "env:"


def load(path: Path) -> LoadedConfig:
    """Read, validate, and resolve the configuration at *path*.

    Raises :class:`ConfigError` with every problem found. Configuration errors are
    always fatal (FR-210) - FCAS must never start on silent defaults.
    """
    raw = _read(path)
    config = _validate(raw, path)
    resolved = _merge_cameras(config)
    password = _resolve_secret(config.rabbitmq.password_ref)
    warnings = _collect_warnings(config, resolved, password)

    return LoadedConfig(
        config=config,
        resolved_cameras=resolved,
        broker_password=password,
        warnings=warnings,
    )


def _read(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            ErrorCode.E_CFG_FILE_NOT_FOUND,
            f"configuration file not found: {path}",
        ) from None
    except OSError as exc:
        raise ConfigError(
            ErrorCode.E_CFG_PARSE_FAILED,
            f"cannot read configuration file {path}: {exc}",
        ) from exc

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            ErrorCode.E_CFG_PARSE_FAILED,
            f"configuration file {path} is not valid JSON: "
            f"{exc.msg} at line {exc.lineno} column {exc.colno}",
        ) from exc

    if not isinstance(parsed, dict):
        raise ConfigError(
            ErrorCode.E_CFG_PARSE_FAILED,
            f"configuration file {path} must contain a JSON object at the top level",
        )
    return parsed


def _validate(raw: dict[str, Any], path: Path) -> Config:
    issues: list[str] = []

    config: Config | None = None
    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        issues.extend(_render_pydantic(exc))

    issues.extend(_structural_issues(raw))

    if issues:
        raise ConfigError(
            ErrorCode.E_CFG_INVALID_VALUE,
            f"configuration file {path} has {len(issues)} problem(s)",
            tuple(issues),
        )

    if config is None:  # pragma: no cover - unreachable while issues is empty
        raise ConfigError(ErrorCode.E_CFG_INVALID_VALUE, f"configuration file {path} is invalid")
    return config


def _render_pydantic(exc: ValidationError) -> list[str]:
    """Turn pydantic's error list into ``field.path: message`` lines."""
    rendered: list[str] = []
    for error in exc.errors():
        rendered.append(f"{_field_path(error['loc'])}: {error['msg']}")
    return rendered


def _field_path(loc: tuple[int | str, ...]) -> str:
    """``('cameras', 1, 'exposureUs')`` -> ``cameras[1].exposureUs``."""
    if not loc:
        return "<document>"
    parts: list[str] = [str(loc[0])]
    for item in loc[1:]:
        parts.append(f"[{item}]" if isinstance(item, int) else f".{item}")
    return "".join(parts)


def _structural_issues(raw: dict[str, Any]) -> list[str]:
    """Duplicate serial / position detection, tolerant of a malformed document.

    Runs on raw data so it reports alongside field errors rather than after them.
    """
    cameras = raw.get("cameras")
    if not isinstance(cameras, list):
        return []

    issues: list[str] = []
    for key, label in (("serial", "serial"), ("position", "position")):
        values = [
            entry[key]
            for entry in cameras
            if isinstance(entry, dict) and isinstance(entry.get(key), str)
        ]
        for value, count in sorted(Counter(values).items()):
            if count > 1:
                indexes = [
                    i
                    for i, entry in enumerate(cameras)
                    if isinstance(entry, dict) and entry.get(key) == value
                ]
                where = ", ".join(f"cameras[{i}]" for i in indexes)
                issues.append(f"cameras.{label}: duplicate value {value!r} in {where}")
    return issues


def _merge_cameras(config: Config) -> tuple[ResolvedCamera, ...]:
    """Apply ``cameraDefaults`` under each entry's own values."""
    base = config.camera_defaults.model_dump()
    resolved: list[ResolvedCamera] = []
    for entry in config.cameras:
        merged = {**base, **entry.overrides()}
        resolved.append(
            ResolvedCamera(
                serial=entry.serial,
                position=entry.position,
                routing_key=entry.effective_routing_key(),
                settings=CameraSettings.model_validate(merged),
            )
        )
    return tuple(resolved)


def _resolve_secret(reference: str) -> SecretStr | None:
    """Resolve an ``env:NAME`` reference. The value is never logged (NFR-401)."""
    if not reference.startswith(_ENV_PREFIX):
        # A literal password in the file is a security defect, not a convenience.
        raise ConfigError(
            ErrorCode.E_CFG_SECRET_UNRESOLVED,
            "rabbitmq.passwordRef must be an indirect reference of the form "
            "'env:VARIABLE_NAME'; credentials are never stored in the config file",
        )

    name = reference[len(_ENV_PREFIX) :]
    if not name:
        raise ConfigError(
            ErrorCode.E_CFG_SECRET_UNRESOLVED,
            "rabbitmq.passwordRef names no environment variable (expected 'env:NAME')",
        )

    value = os.environ.get(name)
    return SecretStr(value) if value is not None else None


def _collect_warnings(
    config: Config,
    cameras: tuple[ResolvedCamera, ...],
    password: SecretStr | None,
) -> tuple[str, ...]:
    """Non-fatal findings. These load successfully but must be visible."""
    warnings: list[str] = []

    ceiling = config.acquisition.exposure_ceiling_us
    for camera in cameras:
        if camera.settings.exposure_us > ceiling:
            # FR-206: a warning, not an error. The device layer clamps it at apply time.
            warnings.append(
                f"cameras[{camera.position.value}].exposureUs "
                f"{camera.settings.exposure_us:g} exceeds acquisition.exposureCeilingUs "
                f"{ceiling:g}; it will be clamped (FR-206, CON-001 motion blur)"
            )

    if password is None:
        name = config.rabbitmq.password_ref[len(_ENV_PREFIX) :]
        warnings.append(
            f"rabbitmq.passwordRef references environment variable {name!r}, which is "
            f"not set; broker authentication will fail when publishing starts"
        )

    return tuple(warnings)


def summarise(loaded: LoadedConfig) -> list[str]:
    """A credential-free summary of the effective configuration, logged at startup."""
    config = loaded.config
    password_state = "set" if loaded.broker_password else "unset"
    lines = [
        f"rest {config.service.rest_listen_address}:{config.service.rest_port} "
        f"level={config.service.log_level.value}",
        f"broker {config.rabbitmq.host}:{config.rabbitmq.port}{config.rabbitmq.vhost} "
        f"user={config.rabbitmq.username} password=<{password_state}>",
        f"exchange={config.rabbitmq.exchange} maxLength={config.rabbitmq.queue_max_length} "
        f"overflow={config.rabbitmq.queue_overflow} ttl={config.rabbitmq.message_ttl_ms}ms",
        f"acquisition trigger={config.acquisition.trigger_kind.value} "
        f"window={config.acquisition.grouping_window_ms}ms "
        f"queueDepth={config.acquisition.local_queue_depth} "
        f"poolSize={config.acquisition.buffer_pool_size}",
    ]
    for camera in loaded.resolved_cameras:
        lines.append(
            f"camera {camera.position.value:<6} serial={camera.serial} "
            f"key={camera.routing_key} "
            f"{camera.settings.width}x{camera.settings.height} "
            f"exposure={camera.settings.exposure_us:g}us gain={camera.settings.gain_db:g}dB"
        )
    return lines
