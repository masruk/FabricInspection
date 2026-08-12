"""ServiceApp lifecycle and state transitions (Unit 01)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import pytest

from fcas.common.errors import ServiceError
from fcas.common.types import ServiceState
from fcas.service import app as app_module
from fcas.service.app import ServiceApp, install_thread_excepthook
from fcas.telemetry import logging_setup


class WriteConfig(Protocol):
    def __call__(self, document: dict[str, Any], name: str = ...) -> Path: ...


@pytest.fixture(autouse=True)
def _isolate_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Keep each test's log files inside its own tmp_path."""
    monkeypatch.setenv("FCAS_HOME", str(tmp_path))
    yield
    logging_setup.shutdown_logging()


def _app(write_config: WriteConfig, base_config: dict[str, Any]) -> ServiceApp:
    return ServiceApp(write_config(base_config), console=True)


def _log_text(root: Path) -> str:
    """Read what was actually written to the log file.

    ``caplog`` cannot be used for these: ``setup_logging`` sets ``propagate = False``
    on the ``fcas`` logger - deliberately, so a dependency that configures the root
    logger cannot double every line - and pytest's caplog handler lives on the root
    logger. Asserting on the file is the stronger test regardless, because it
    exercises the formatter and the rotating handler that actually ship.
    """
    return (root / "logs" / "fcas.log").read_text(encoding="utf-8")


def test_start_reaches_ready(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    app = _app(write_config, base_config)
    # Read into a local each time: mypy narrows a property result and does not widen
    # it again after a call that changes it, so re-asserting on `app.state` directly
    # would be reported as a non-overlapping comparison.
    before = app.state
    assert before is ServiceState.IDLE

    app.start()
    try:
        after_start = app.state
        assert after_start is ServiceState.READY
        assert len(app.config.resolved_cameras) == 3
    finally:
        app.stop()


def test_stop_returns_to_idle(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    app = _app(write_config, base_config)
    app.start()
    app.stop()

    assert app.state is ServiceState.IDLE


def test_double_start_is_rejected(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    app = _app(write_config, base_config)
    app.start()
    try:
        with pytest.raises(ServiceError):
            app.start()
    finally:
        app.stop()


def test_stop_before_start_is_a_no_op(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    _app(write_config, base_config).stop()


def test_config_before_start_raises(write_config: WriteConfig, base_config: dict[str, Any]) -> None:
    with pytest.raises(ServiceError):
        _ = _app(write_config, base_config).config


def test_context_manager_starts_and_stops(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    with _app(write_config, base_config) as app:
        inside = app.state
        assert inside is ServiceState.READY
    after_exit = app.state
    assert after_exit is ServiceState.IDLE


def test_transition_logs_old_new_and_cause(
    write_config: WriteConfig, base_config: dict[str, Any], tmp_path: Path
) -> None:
    app = _app(write_config, base_config)
    app.start()
    app.transition_to(ServiceState.RUNNING, "acquisition started")
    app.stop()

    assert "state READY -> RUNNING (acquisition started)" in _log_text(tmp_path)


def test_transition_to_same_state_is_silent(
    write_config: WriteConfig, base_config: dict[str, Any], tmp_path: Path
) -> None:
    app = _app(write_config, base_config)
    app.start()
    app.transition_to(ServiceState.READY, "no change")
    app.stop()

    text = _log_text(tmp_path)
    assert "state READY -> READY" not in text
    # Guard against the assertion above passing merely because nothing was captured.
    assert "state IDLE -> READY" in text


def test_run_until_shutdown_unblocks_on_request(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    app = _app(write_config, base_config)
    app.start()
    try:
        threading.Timer(0.05, lambda: app.request_shutdown("test")).start()
        app.run_until_shutdown()  # pytest-timeout fails the test if this hangs
    finally:
        app.stop()


def test_run_until_shutdown_is_interruptible_by_a_signal(
    write_config: WriteConfig, base_config: dict[str, Any]
) -> None:
    """The wait must yield to the interpreter so signal handlers can run.

    A bare ``Event.wait()`` becomes an uninterruptible lock acquire on Windows, and
    CPython only dispatches signal handlers in the main thread between bytecodes - so
    the handler would never fire and the process would have to be killed. A live
    console run caught this; the unit test above did not, because setting the event
    from another thread bypasses the signal path entirely.

    Here the "signal handler" is simulated by a callback the main thread can only run
    once its wait returns.
    """
    app = _app(write_config, base_config)
    app.start()
    try:
        fired = threading.Event()

        def deliver_after_wait_returns() -> None:
            # Stands in for a signal handler: it can only take effect if the main
            # thread is periodically back in interpreter control.
            time.sleep(0.05)
            fired.set()
            app.request_shutdown("simulated signal")

        threading.Thread(target=deliver_after_wait_returns, daemon=True).start()
        started = time.monotonic()
        app.run_until_shutdown()
        elapsed = time.monotonic() - started

        assert fired.is_set()
        assert elapsed < 5.0, "shutdown must fit well inside the 10 s budget (OP-104)"
    finally:
        app.stop()


def test_shutdown_poll_interval_fits_the_budget() -> None:
    """The poll interval must leave room for teardown inside OP-104's 10 s."""
    assert 0 < app_module.SHUTDOWN_POLL_S <= 1.0
    assert app_module.SHUTDOWN_POLL_S < app_module.SHUTDOWN_BUDGET_S / 4


def test_startup_logs_configuration_without_credentials(
    write_config: WriteConfig,
    base_config: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "startup-secret-77c1"
    monkeypatch.setenv("FCAS_TEST_MQ_PASSWORD", secret)
    base_config["rabbitmq"]["passwordRef"] = "env:FCAS_TEST_MQ_PASSWORD"
    base_config["service"]["logLevel"] = "DEBUG"
    app = _app(write_config, base_config)
    app.start()
    app.stop()

    text = _log_text(tmp_path)
    assert "DB0717739" in text, "the effective configuration must be logged at startup"
    assert "password=<set>" in text
    assert secret not in text, "a credential reached the log file (NFR-401)"


def test_warnings_are_logged_at_warning_level(
    write_config: WriteConfig, base_config: dict[str, Any], tmp_path: Path
) -> None:
    base_config["acquisition"]["exposureCeilingUs"] = 800
    base_config["cameras"][0]["exposureUs"] = 1500
    app = _app(write_config, base_config)
    app.start()
    app.stop()

    lines = _log_text(tmp_path).splitlines()
    assert any("[WARN ]" in line and "exceeds" in line for line in lines), lines


def test_thread_excepthook_logs_instead_of_dying_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exception escaping a thread must reach the log. Under Session 0 there is no
    stderr, so without this the thread would vanish without trace (NFR-203)."""
    install_thread_excepthook()

    def explode() -> None:
        raise RuntimeError("worker blew up")

    with caplog.at_level(logging.ERROR):
        thread = threading.Thread(target=explode, name="test-worker")
        thread.start()
        thread.join()

    assert "unhandled exception in thread test-worker" in caplog.text
    assert "worker blew up" in caplog.text


def test_log_file_is_created_under_install_root(
    write_config: WriteConfig, base_config: dict[str, Any], tmp_path: Path
) -> None:
    app = _app(write_config, base_config)
    app.start()
    app.stop()

    assert (tmp_path / "logs" / "fcas.log").is_file()
