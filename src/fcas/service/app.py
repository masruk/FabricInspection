"""Application lifecycle orchestration.

``ServiceApp`` is hosting-independent: the same object runs under ``fcas run
--console`` and, from Unit 09, under the Windows SCM. Nothing here may call an SCM
API - if service hosting ever needs a change in this file, the boundary is wrong.

Subsystems are started in dependency order and torn down in reverse. Units 02-10 add
their subsystems at the marked points without restructuring this file.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from types import TracebackType

from fcas.common import paths
from fcas.common.errors import ErrorCode, ServiceError
from fcas.common.types import ServiceState
from fcas.common.version import runtime_summary, version
from fcas.config import loader
from fcas.config.schema import LoadedConfig
from fcas.telemetry import logging_setup

log = logging.getLogger(__name__)

#: OP-104 - a stop request must complete within this budget.
SHUTDOWN_BUDGET_S = 10.0

#: How often the main thread surfaces from its wait to run pending signal handlers.
#: See :meth:`ServiceApp.run_until_shutdown` for why this cannot be an untimed wait.
SHUTDOWN_POLL_S = 0.25


def install_thread_excepthook() -> None:
    """Route uncaught thread exceptions into the log.

    Without this, an exception escaping a thread prints to stderr and the thread dies
    quietly - and under Session 0 there is no stderr, so a camera would simply stop
    producing frames with no trace anywhere. Each thread body still has its own guard
    (invariant 6); this is the backstop for the case that misses.
    """

    def hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "<unknown>"
        if args.exc_value is None:
            # Possible during interpreter teardown; still worth a line rather than
            # losing the fact that a thread died.
            log.error("unhandled exception in thread %s (no exception value)", name)
            return
        log.error(
            "unhandled exception in thread %s: %s",
            name,
            args.exc_value,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = hook


class ServiceApp:
    """Owns configuration, logging, service state, and the shutdown signal."""

    def __init__(self, config_path: Path, *, console: bool) -> None:
        self._config_path = config_path
        self._console = console
        self._state = ServiceState.IDLE
        self._shutdown = threading.Event()
        self._started = False
        self._loaded: LoadedConfig | None = None

    # -- properties ---------------------------------------------------------

    @property
    def state(self) -> ServiceState:
        return self._state

    @property
    def config(self) -> LoadedConfig:
        if self._loaded is None:
            raise ServiceError(ErrorCode.E_SVC_NOT_STARTED, "configuration has not been loaded")
        return self._loaded

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Load configuration, bring subsystems up, and reach ``READY``.

        Configuration is loaded *before* logging is configured, because the log
        destination and level come from it. Failures before that point surface as a
        raised :class:`~fcas.common.errors.ConfigError` and are reported by the caller.
        """
        if self._started:
            raise ServiceError(ErrorCode.E_SVC_ALREADY_STARTED, "service is already started")

        loaded = loader.load(self._config_path)
        self._loaded = loaded

        logging_setup.setup_logging(
            level=loaded.config.service.log_level,
            log_dir=paths.resolve(loaded.config.service.log_dir),
            max_bytes=loaded.config.service.log_max_bytes,
            backup_count=loaded.config.service.log_backup_count,
            console=self._console,
        )
        install_thread_excepthook()

        log.info("FCAS %s starting (%s)", version(), "console" if self._console else "service")
        log.info("runtime %s", runtime_summary())
        log.info("install root %s", paths.install_root())
        log.info("config %s", self._config_path)
        for line in loader.summarise(loaded):
            log.info("config %s", line)
        for warning in loaded.warnings:
            log.warning("config %s", warning)

        # Unit 02 initialises the MVS SDK here.
        # Unit 03 performs the memory budget check and allocates the buffer pool.
        # Unit 04 starts CameraManager; Unit 05 the correlator; Unit 06 the publisher;
        # Unit 08 the REST server; Unit 10 the health monitor, then gc.freeze().

        self._started = True
        # Unit 01 has no camera subsystem, so READY here means "configuration is valid
        # and nothing else exists yet". SRS 6.1 gates IDLE -> READY on at least one
        # camera being open; Unit 04 tightens this to match. See progress-tracker.md.
        self.transition_to(ServiceState.READY, "configuration loaded")

    def stop(self) -> None:
        """Tear down in reverse order. Must complete within the shutdown budget."""
        if not self._started:
            return

        # Unit 10 stops the health monitor here, then Unit 08 the REST server,
        # Unit 06 the publisher, Unit 05 the correlator, Unit 04 CameraManager,
        # Unit 03 the buffer pool, Unit 02 the SDK - reverse of start().

        self.transition_to(ServiceState.IDLE, "service stopping")
        log.info("FCAS %s stopped", version())
        self._started = False
        logging_setup.shutdown_logging()

    def run_until_shutdown(self) -> None:
        """Block until :meth:`request_shutdown` is called.

        The poll loop is **not** redundant. ``Event.wait()`` with no timeout ends up in
        an uninterruptible lock acquire on Windows, and CPython only runs signal
        handlers in the main thread between bytecodes - so a bare ``wait()`` never sees
        Ctrl+C, SIGTERM, or a supervisor's CTRL_BREAK_EVENT, and the process has to be
        killed. Waiting with a timeout hands control back to the interpreter regularly
        so pending handlers actually run.

        This is what lets shutdown fit the 10 s budget (OP-104), and Unit 09's SvcStop
        depends on the same behaviour. Do not "simplify" this to ``wait()``.
        """
        while not self._shutdown.wait(SHUTDOWN_POLL_S):
            pass

    def request_shutdown(self, reason: str) -> None:
        """Signal a graceful stop. Safe to call from a signal handler or another thread."""
        if not self._shutdown.is_set():
            log.info("shutdown requested (%s)", reason)
            self._shutdown.set()

    # -- state machine ------------------------------------------------------

    def transition_to(self, new_state: ServiceState, cause: str) -> None:
        """Change state, logging old, new, and cause (ui-context.md)."""
        if new_state is self._state:
            return
        old, self._state = self._state, new_state
        log.info("state %s -> %s (%s)", old.value, new_state.value, cause)

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> ServiceApp:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
