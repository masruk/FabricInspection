"""Configuration schema (SRS 5.4).

JSON field names are ``lowerCamelCase`` - that spelling is the contract with whoever
edits the file, and it does not change because the implementation language did.
Python attributes are ``snake_case`` via pydantic aliases.

``extra="forbid"`` everywhere: a typo'd key must be an error naming the key, not a
setting that silently does nothing for the next six months.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from fcas.common.types import (
    BayerQuality,
    CameraPosition,
    LogLevel,
    TriggerActivation,
    TriggerKind,
)

_STRICT = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class ServiceConfig(BaseModel):
    model_config = _STRICT

    rest_listen_address: str = Field("127.0.0.1", alias="restListenAddress", min_length=1)
    rest_port: int = Field(8080, alias="restPort", ge=1, le=65535)
    log_level: LogLevel = Field(LogLevel.INFO, alias="logLevel")
    log_dir: str = Field("logs", alias="logDir", min_length=1)
    log_max_bytes: int = Field(52_428_800, alias="logMaxBytes", gt=0)
    log_backup_count: int = Field(20, alias="logBackupCount", ge=0)
    max_memory_budget_mb: int = Field(900, alias="maxMemoryBudgetMB", gt=0)
    diagnostic_image_dir: str = Field("diagnostics", alias="diagnosticImageDir", min_length=1)
    diagnostic_images_enabled: bool = Field(False, alias="diagnosticImagesEnabled")


class RabbitMqConfig(BaseModel):
    model_config = _STRICT

    host: str = Field("127.0.0.1", min_length=1)
    port: int = Field(5672, ge=1, le=65535)
    vhost: str = Field("/", min_length=1)
    username: str = Field("fcas", min_length=1)
    #: Indirect reference of the form ``env:NAME``. The value is resolved at load and
    #: kept in :attr:`Config.broker_password`; it is never stored here and never logged.
    password_ref: str = Field("env:FCAS_MQ_PASSWORD", alias="passwordRef", min_length=1)
    exchange: str = Field("fabric.frames", min_length=1)
    telemetry_exchange: str = Field("fabric.telemetry", alias="telemetryExchange", min_length=1)
    queue_max_length: int = Field(3, alias="queueMaxLength", gt=0)
    queue_overflow: str = Field("drop-head", alias="queueOverflow", min_length=1)
    message_ttl_ms: int = Field(5000, alias="messageTtlMs", gt=0)
    publisher_confirms: bool = Field(True, alias="publisherConfirms")
    reconnect_initial_ms: int = Field(1000, alias="reconnectInitialMs", gt=0)
    reconnect_max_ms: int = Field(30_000, alias="reconnectMaxMs", gt=0)


class AcquisitionConfig(BaseModel):
    model_config = _STRICT

    trigger_kind: TriggerKind = Field(TriggerKind.HARDWARE, alias="triggerKind")
    grouping_window_ms: int = Field(200, alias="groupingWindowMs", gt=0)
    local_queue_depth: int = Field(4, alias="localQueueDepth", gt=0)
    buffer_pool_size: int = Field(18, alias="bufferPoolSize", gt=0)
    trigger_pitch_mm: float = Field(460.0, alias="triggerPitchMm", gt=0)
    exposure_ceiling_us: float = Field(800.0, alias="exposureCeilingUs", gt=0)
    hotplug_poll_interval_ms: int = Field(3000, alias="hotplugPollIntervalMs", gt=0)
    watchdog_timeout_ms: int = Field(28_000, alias="watchdogTimeoutMs", gt=0)
    expect_triggers: bool = Field(True, alias="expectTriggers")


class CameraSettings(BaseModel):
    """Fully-resolved per-camera settings: defaults with overrides already applied."""

    model_config = _STRICT

    width: int = Field(2448, gt=0)
    height: int = Field(2048, gt=0)
    offset_x: int = Field(0, alias="offsetX", ge=0)
    offset_y: int = Field(0, alias="offsetY", ge=0)
    exposure_us: float = Field(700.0, alias="exposureUs", gt=0)
    gain_db: float = Field(6.0, alias="gainDb", ge=0)
    contrast: int = Field(0)
    gamma: float | None = Field(None, gt=0)
    auto_white_balance: bool = Field(False, alias="autoWhiteBalance")
    bayer_quality: BayerQuality = Field(BayerQuality.BALANCED, alias="bayerQuality")
    trigger_source: str = Field("Line0", alias="triggerSource", min_length=1)
    trigger_activation: TriggerActivation = Field(
        TriggerActivation.RISING_EDGE, alias="triggerActivation"
    )
    trigger_delay_us: float = Field(0.0, alias="triggerDelayUs", ge=0)
    debounce_us: float = Field(50.0, alias="debounceUs", ge=0)
    free_run_fps: float = Field(5.0, alias="freeRunFps", gt=0)

    @property
    def frame_bytes(self) -> int:
        """RGB8 frame size. The unit of the buffer pool budget (SDD 5.4)."""
        return self.width * self.height * 3


class CameraEntry(BaseModel):
    """One configured camera: identity plus optional overrides of the defaults.

    Every settings field is ``None`` here meaning "inherit"; the loader merges them
    with ``cameraDefaults`` to produce a :class:`CameraSettings`.
    """

    model_config = _STRICT

    serial: str = Field(min_length=1)
    position: CameraPosition
    routing_key: str | None = Field(None, alias="routingKey", min_length=1)

    @field_validator("position")
    @classmethod
    def _reject_unknown(cls, value: CameraPosition) -> CameraPosition:
        """``UNKNOWN`` is the internal sentinel for a discovered camera whose serial is
        not configured (FR-105). It is never something an operator can assign, so the
        enum alone is too permissive here."""
        if value is CameraPosition.UNKNOWN:
            allowed = ", ".join(p.value for p in CameraPosition.configurable())
            raise ValueError(f"UNKNOWN is not assignable; expected one of [{allowed}]")
        return value

    width: int | None = Field(None, gt=0)
    height: int | None = Field(None, gt=0)
    offset_x: int | None = Field(None, alias="offsetX", ge=0)
    offset_y: int | None = Field(None, alias="offsetY", ge=0)
    exposure_us: float | None = Field(None, alias="exposureUs", gt=0)
    gain_db: float | None = Field(None, alias="gainDb", ge=0)
    contrast: int | None = None
    gamma: float | None = Field(None, gt=0)
    auto_white_balance: bool | None = Field(None, alias="autoWhiteBalance")
    bayer_quality: BayerQuality | None = Field(None, alias="bayerQuality")
    trigger_source: str | None = Field(None, alias="triggerSource", min_length=1)
    trigger_activation: TriggerActivation | None = Field(None, alias="triggerActivation")
    trigger_delay_us: float | None = Field(None, alias="triggerDelayUs", ge=0)
    debounce_us: float | None = Field(None, alias="debounceUs", ge=0)
    free_run_fps: float | None = Field(None, alias="freeRunFps", gt=0)

    def overrides(self) -> dict[str, object]:
        """The settings fields this entry actually specifies."""
        skip = {"serial", "position", "routing_key"}
        return {
            name: value
            for name, value in self.__dict__.items()
            if name not in skip and value is not None
        }

    def effective_routing_key(self) -> str:
        return self.routing_key or f"camera.{self.position.value.lower()}"


class ResolvedCamera(BaseModel):
    """A camera entry with defaults merged in. What the rest of the service consumes."""

    model_config = ConfigDict(frozen=True)

    serial: str
    position: CameraPosition
    routing_key: str
    settings: CameraSettings


class Config(BaseModel):
    """Root configuration document."""

    model_config = _STRICT

    service: ServiceConfig = Field(default_factory=ServiceConfig)
    rabbitmq: RabbitMqConfig = Field(default_factory=RabbitMqConfig)
    acquisition: AcquisitionConfig = Field(default_factory=AcquisitionConfig)
    camera_defaults: CameraSettings = Field(default_factory=CameraSettings, alias="cameraDefaults")
    cameras: list[CameraEntry] = Field(min_length=1)


class LoadedConfig(BaseModel):
    """The validated document plus everything derived at load time.

    Kept separate from :class:`Config` so the raw document stays a faithful mirror of
    the file while resolved secrets and merged cameras live alongside it.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    config: Config
    resolved_cameras: tuple[ResolvedCamera, ...]
    #: Resolved from ``rabbitmq.passwordRef``. ``None`` when the referenced environment
    #: variable is unset - a warning at load, fatal only when Unit 06 needs it.
    broker_password: SecretStr | None = None
    #: Non-fatal findings surfaced at load, e.g. an exposure above the ceiling (FR-206).
    warnings: tuple[str, ...] = ()
