"""``fcas`` entry point.

The command surface is fixed by ui-context.md. Unit 01 implements ``run --console``
and ``version``; the remaining subcommands are declared here so the surface is stable
from the start, and each reports which unit builds it rather than being invented early.

Exit codes: 0 clean, 1 runtime failure, 2 usage error, 3 configuration invalid.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from types import FrameType

from fcas.common import paths
from fcas.common.errors import ConfigError, FcasError
from fcas.common.version import runtime_summary, version
from fcas.service.app import ServiceApp

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3

_PENDING = {
    "list-cameras": "Unit 02",
    "capture": "Unit 03",
    "measure-skew": "Unit 12",
    "install": "Unit 09",
    "uninstall": "Unit 09",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcas",
        description="Fabric Camera Acquisition Service",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    def with_config(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument(
            "--config",
            type=Path,
            default=None,
            metavar="<path>",
            help="configuration file (default: config/fcas.config.json under the install root)",
        )
        return sub

    run = with_config(subcommands.add_parser("run", help="run the service"))
    run.add_argument(
        "--console",
        action="store_true",
        help="run in the foreground with console logging (the development loop)",
    )
    run.add_argument(
        "--mock-cameras",
        type=int,
        default=0,
        metavar="N",
        help="add N synthetic cameras alongside any real ones (Unit 05)",
    )

    subcommands.add_parser("version", help="print the version and exit")

    with_config(subcommands.add_parser("list-cameras", help="enumerate cameras (Unit 02)"))
    capture = with_config(subcommands.add_parser("capture", help="diagnostic capture (Unit 03)"))
    capture.add_argument("count", type=int, help="number of frames to capture")
    skew = with_config(subcommands.add_parser("measure-skew", help="inter-camera skew (Unit 12)"))
    skew.add_argument("count", type=int, help="number of trigger events to sample")
    install = with_config(subcommands.add_parser("install", help="register the service (Unit 09)"))
    install.add_argument("--account", default=None, metavar="<name>", help="service account")
    subcommands.add_parser("uninstall", help="remove the service (Unit 09)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_usage(sys.stderr)
        print("error: a command is required", file=sys.stderr)
        return EXIT_USAGE

    if args.command == "version":
        print(f"fcas {version()}")
        print(runtime_summary())
        return EXIT_OK

    if args.command in _PENDING:
        print(
            f"error: '{args.command}' is not implemented yet; it is built in "
            f"{_PENDING[args.command]}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.command == "run":
        return _run(args)

    parser.print_usage(sys.stderr)
    return EXIT_USAGE


def _run(args: argparse.Namespace) -> int:
    if not args.console:
        print(
            "error: only --console is supported so far; SCM hosting is built in Unit 09",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.mock_cameras:
        print(
            "error: --mock-cameras is not implemented yet; it is built in Unit 05", file=sys.stderr
        )
        return EXIT_USAGE

    config_path = args.config if args.config is not None else paths.default_config_path()
    app = ServiceApp(Path(config_path), console=True)

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        app.request_shutdown(f"signal {signal.Signals(signum).name}")

    try:
        app.start()
    except ConfigError as exc:
        # Logging may not be configured yet - configuration is what tells us where to
        # log to. Report to stderr so a console operator sees every problem at once.
        print(f"error: {exc}", file=sys.stderr)
        for issue in exc.issues:
            print(f"  {issue}", file=sys.stderr)
        return EXIT_CONFIG
    except FcasError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_RUNTIME

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    # Windows delivers Ctrl+Break, and CTRL_BREAK_EVENT from a parent process, as
    # SIGBREAK rather than SIGINT. Without this, a supervisor that signals the process
    # group would kill the service outright instead of letting it shut down cleanly.
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, handle_signal)

    try:
        app.run_until_shutdown()
        return EXIT_OK
    except FcasError as exc:
        log.error("fatal: %s", exc)
        return EXIT_RUNTIME
    finally:
        app.stop()


if __name__ == "__main__":
    sys.exit(main())
