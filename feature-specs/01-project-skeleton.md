# Unit 01: Package Skeleton, Config, Logging, Console Mode

## Goal

Create the Python package with its `src/` layout and entry points, a `fcas run --console` that
loads and validates a JSON configuration file, initialises structured logging, and exits cleanly.
Invalid configuration is fatal and names every offending field. No camera or broker code.

## Design

Console-first. `fcas run --console` is the development entry point and stays working for the life
of the project. The Windows Service wrapper comes in Unit 09 and must not be introduced here.

All output follows the log format in `ui-context.md`: timestamp with milliseconds, five-character
padded level, seven-character padded component tag. Tags used in this unit: `service`, `config `.

Error codes follow `E_<DOMAIN>_<REASON>` from `code-standards.md`. This unit establishes the
`FcasError` hierarchy every later unit raises, and the three quality gates (`ruff`, `mypy
--strict`, `pytest`) that every later unit must keep green.

## Implementation

### Prerequisite

Unit 00 in `00-build-plan.md` must be complete: CPython 3.12 **64-bit** installed, venv created,
and the MVS binding confirmed importable. Do not start this unit before that check passes.

### Packaging

Create `pyproject.toml` at the repo root with:

- Project metadata, `requires-python = ">=3.12,<3.13"`
- `src/` layout (`[tool.setuptools.packages.find] where = ["src"]` or the hatchling equivalent)
- Runtime dependency: `pydantic>=2`
- Dev dependencies: `pytest`, `pytest-timeout`, `mypy`, `ruff`
- Entry points:

```toml
[project.scripts]
fcas    = "fcas.__main__:main"
fcasctl = "fcas.fcasctl.__main__:main"
```

- `[tool.ruff]` with the rule set enabled, line length 100, and a **banned-imports rule** reserving
  `MvImport` to `fcas.camera.mvs_sdk` and `pika` to `fcas.publish` — configure it now so the
  boundary is enforced from the first commit rather than retrofitted.
- `[tool.mypy]` with `strict = true` over `src` and `tests`.
- `[tool.pytest.ini_options]` registering the `broker` marker and a default timeout.

Generate `requirements.lock` with exact pinned versions (`pip freeze` or `pip-compile`). Install
with `pip install -e ".[dev]"`.

Do not add `pika`, `flask`, `waitress`, `requests`, or `pywin32` yet.

### `.gitignore`

`.venv/`, `__pycache__/`, `*.pyc`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `build/`,
`dist/`, `*.egg-info/`, `logs/`, `diagnostics/`.

### `src/fcas/common`

`errors.py` — `ErrorCode` enum covering the `E_CFG_*` and `E_SVC_*` domains, and the `FcasError`
base carrying `code`, `message`, `sdk_ret`, and `amqp_ret`. Subclasses `ConfigError` and
`ServiceError`. `sdk_ret` and `amqp_ret` are unused here, but fix the shape now so later units do
not churn it.

`version.py` — version string plus an accessor. Read it from the package metadata rather than
duplicating it in two places.

`types.py` — `CameraPosition` enum (`LEFT`, `CENTER`, `RIGHT`, `UNKNOWN`) with string conversion
both ways, and the `ServiceState` enum from `ui-context.md`. Position parsing is case-sensitive
and rejects anything not in the enum.

`paths.py` — resolve a possibly-relative path against the **package installation directory**, not
`os.getcwd()`. Unit 09 depends on this being right from the start; retrofitting it after the
service fails to find its config is the standard way this bug gets found.

### `src/fcas/config`

`schema.py` — pydantic v2 models mirroring SRS §5.4: `ServiceConfig`, `RabbitMqConfig`,
`AcquisitionConfig`, `CameraSettings`, `CameraEntry`, root `Config`. JSON field names stay
`lowerCamelCase` (the file format is the contract); Python attributes are `snake_case` via
`alias` + `populate_by_name`. Include the RabbitMQ and acquisition fields now even though nothing
reads them yet — the schema comes from the SRS, and later units should find their fields already
present.

`loader.py` — read the file, merge `cameraDefaults` into each `cameras` entry with per-camera
values overriding defaults, then validate.

Validation runs over the whole config and collects **all** errors before returning, never failing
on the first. pydantic already does this; the job here is to render its `ValidationError` into the
project's log format, one line per error, each naming the field path
(`cameras[1].exposureUs: must be positive, got -1`).

Rules enforced in this unit:

- `cameras` non-empty; every entry has a non-empty `serial` and a valid `position`
- serials unique; positions unique
- `width`, `height`, `offsetX`, `offsetY` non-negative, width/height non-zero
- `exposureUs` positive; exceeding `acquisition.exposureCeilingUs` is a **warning**, not an
  error (FR-206)
- `gainDb` not negative
- `queueMaxLength`, `localQueueDepth`, `messageTtlMs`, `groupingWindowMs`, `bufferPoolSize`
  positive
- `restPort` and broker `port` in 1–65535
- `logLevel` one of `ERROR`, `WARN`, `INFO`, `DEBUG`
- `triggerKind` one of `HARDWARE`, `SOFTWARE`, `FREERUN`

Credentials are referenced indirectly. A `passwordRef` of form `env:NAME` resolves from the
environment at load. Store the resolved value in a field whose `repr` is redacted (pydantic
`SecretStr`) so it cannot reach a log line by accident.

### `src/fcas/telemetry/logging_setup.py`

Created in this unit despite the package otherwise belonging to Unit 10 — logging is needed from
the first line of code.

- A custom `logging.Formatter` producing the exact line format from `ui-context.md`, including
  padding. The component tag derives from the logger's package (`fcas.camera.*` → `camera `), so
  call sites never pass it by hand.
- `RotatingFileHandler` with path, size, and backup count from config.
- A console handler attached **only** in console mode. Unit 09 depends on this switch existing.
- Level from `logLevel`.
- Call sites use `logging.getLogger(__name__)` and lazy `%s` formatting.

### `src/fcas/service/app.py`

`ServiceApp` — the hosting-independent orchestrator. In this unit: load config, validate, init
logging, log a configuration summary with credentials redacted, log version and interpreter
version, transition to `READY`, wait for shutdown on an `Event`, tear down in reverse order.
Structure `start()` and `stop()` so later units add subsystems without restructuring.

Owns `ServiceState` and a `transition_to()` that logs old state, new state, and cause.

Install `threading.excepthook` here so it is in place before any unit adds a thread.

### `src/fcas/__main__.py`

`argparse` with subcommands, matching the surface in `ui-context.md`. This unit implements
`run --console`, `version`, and the `--config` option; the rest are declared and exit with a
"not implemented in this unit" message rather than being invented early.

No arguments prints usage and exits 2. `SIGINT` requests graceful shutdown.
Default config path is `config/fcas.config.json` resolved via `paths.py`.

Exit codes: `0` clean, `1` runtime failure, `2` usage error, `3` configuration invalid.

### `config/fcas.config.json`

Commit a working default matching SRS §5.4, with known serial `DB0717739` mapped to `LEFT` and
two clearly-marked placeholder entries for `CENTER` and `RIGHT`.

### `tests/unit/test_config.py`

Cover: valid config loads; defaults merge and per-camera values override; duplicate serial
rejected; duplicate position rejected; invalid position string rejected; out-of-range port
rejected; invalid log level rejected; exposure above ceiling warns but still loads; multiple
simultaneous errors all reported in one run; `env:` reference resolves and never appears in log
output or in `repr()` of the config object.

### `tests/unit/test_paths.py`

Cover: a relative path resolves against the package directory, not the process CWD (change the
CWD in the test and assert it makes no difference); an absolute path passes through unchanged.

## Dependencies

- Runtime: `pydantic`
- Dev: `pytest`, `pytest-timeout`, `mypy`, `ruff`

## Verify when done

- [ ] `pip install -e ".[dev]"` succeeds in a clean venv
- [ ] `ruff check .` and `ruff format --check .` pass with zero findings
- [ ] `mypy --strict src tests` passes with zero errors
- [ ] `pytest` passes
- [ ] `fcas version` prints the version and exits 0
- [ ] `fcas` with no arguments prints usage and exits 2
- [ ] `fcas run --console` loads config, logs startup, reaches `READY`, exits 0 on Ctrl+C
- [ ] `fcas run --console --config <bad file>` reports every invalid field by path and exits 3
- [ ] A config with a duplicate serial and an invalid position reports **both** errors in one run
- [ ] Log output matches `ui-context.md` exactly, including padding
- [ ] Rotating log file is created and rotates at the configured size
- [ ] No credential value appears in any log line, console output, or `repr()` of the config
- [ ] Running from a different working directory finds the config and writes logs to the same
      place — path resolution does not depend on CWD
- [ ] No `MvImport` or `pika` import exists anywhere; the ruff banned-import rule is configured
      and active
- [ ] `print()` appears nowhere in `src/`
- [ ] `.gitignore` excludes all build and cache output; `git status` is clean after a test run
- [ ] `context/progress-tracker.md` updated; committed as `feat(unit-01): package skeleton, config, logging, console mode`
