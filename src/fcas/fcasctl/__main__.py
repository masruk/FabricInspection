"""``fcasctl`` entry point - a thin client over the REST control API.

Unit 08 builds the real command set. This unit declares the entry point so the
console script resolves from the first install, and implements only ``version``.

The CLI contains no logic of its own by design: there is exactly one implementation
of every operation, behind the REST API, so the CLI can never drift from it.

Exit codes: 0 success, 1 operation failure, 2 usage error.
"""

from __future__ import annotations

import sys

from fcas.common.version import version

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

_USAGE = """usage: fcasctl <command> [options]

commands:
  version                      print the client version

Every other command (status, cameras, config, start, stop, trigger, roll) is
built in Unit 08 against the REST control API.
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv

    if not args:
        print(_USAGE, file=sys.stderr)
        return EXIT_USAGE

    if args[0] == "version":
        print(f"fcasctl {version()}")
        return EXIT_OK

    print(f"error: '{args[0]}' is not implemented yet; it is built in Unit 08", file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
