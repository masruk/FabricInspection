# Unit 01: Solution Skeleton, Config, Logging, Console Mode

## Goal

Create the Visual Studio solution with four projects, a `Fcas.exe` that runs in console mode,
loads and validates a JSON configuration file, initialises structured logging, and exits cleanly.
Invalid configuration is fatal and names every offending field. No camera or broker code.

## Design

Console-first. `--console` is the development entry point and stays working for the life of the
project. The Windows Service wrapper comes in Unit 09 and must not be introduced here.

All output follows the log format in `ui-context.md`: timestamp with milliseconds, five-character
padded level, seven-character padded component tag. Tags used in this unit: `service`, `config `.

Error codes follow `E_<DOMAIN>_<REASON>` from `code-standards.md`. This unit establishes the
`Status` type every later unit returns.

## Implementation

### Solution and projects

Create `FabricInspection.sln` with four projects per `architecture.md`:

| Project | Type | Contents |
| --- | --- | --- |
| `FcasCore` | Static library | All of `src/` except `main.cpp` |
| `Fcas` | Console app | `main.cpp`, links `FcasCore` |
| `FcasCtl` | Console app | Stub only in this unit — prints version and exits |
| `FcasTests` | Console app | GoogleTest runner, links `FcasCore` |

Configure x64 only, Debug and Release, toolset v145, `/std:c++17`, warning level 4 with the
project's own warnings as errors. Remove the Win32 platform entirely.

Create `props/Common.props` holding settings shared by all projects and all configurations:
output directory `build\$(Platform)\$(Configuration)\`, intermediate directory outside the source
tree, C++ language standard, warning level. Import it from every `.vcxproj`. Do not duplicate
these settings per configuration.

Create `.vcxproj.filters` for each project so Solution Explorer mirrors the on-disk folders.

### Dependencies

Use vcpkg in manifest mode with Visual Studio integration. Create `vcpkg.json` at the repo root
declaring only what this unit needs:

- `nlohmann-json` — configuration parsing
- `spdlog` — structured logging with rotation
- `gtest` — unit tests

Do not add rabbitmq-c, cpp-httplib, or anything else yet.

Enable vcpkg manifest mode in the project properties. Verify a clean clone restores packages and
builds without manual steps.

### `.gitignore`

Add entries for `.vs/`, `build/`, `x64/`, `*.user`, `vcpkg_installed/`, and MSBuild intermediates.

### `src/common`

`Error.h` — `ErrorCode` enum covering the `E_CFG_*` and `E_SVC_*` domains, and the `Status` struct
carrying `code`, `sdkRet`, `amqpRet`, and `message`, with an `ok()` accessor. `sdkRet` and
`amqpRet` are unused here, but fix the shape now so later units do not churn it.

`Version.h` — compile-time version string plus a `Version()` accessor.

`Types.h` — `CameraPosition` enum (`LEFT`, `CENTER`, `RIGHT`, `UNKNOWN`) with string conversion
both ways, and the `ServiceState` enum from `ui-context.md`. Position parsing is case-sensitive
and rejects anything not in the enum.

### `src/config`

`Config.h/.cpp` — plain structs mirroring SRS §5.4: `ServiceConfig`, `RabbitMqConfig`,
`AcquisitionConfig`, `CameraSettings`, `CameraEntry`, root `Config`. Include the RabbitMQ and
acquisition fields now even though nothing reads them yet — the schema comes from the SRS, and
later units should find their fields already present.

Loading merges `cameraDefaults` into each `cameras` entry, per-camera values overriding defaults.

`ConfigValidator.h/.cpp` — validation runs over the whole config and collects **all** errors
before returning, never failing on the first. Each error names the field path, e.g.
`cameras[1].exposureUs`.

Rules enforced in this unit:

- `cameras` non-empty; every entry has a non-empty `serial` and a valid `position`
- serials unique; positions unique
- `width`, `height`, `offsetX`, `offsetY` non-negative, width/height non-zero
- `exposureUs` positive; exceeding `acquisition.exposureCeilingUs` is a **warning**, not an
  error (FR-206)
- `gainDb` not negative
- `queueMaxLength`, `localQueueDepth`, `messageTtlMs`, `groupingWindowMs` positive
- `restPort` and broker `port` in 1–65535
- `logLevel` one of `ERROR`, `WARN`, `INFO`, `DEBUG`
- `triggerKind` one of `HARDWARE`, `SOFTWARE`, `FREERUN`

Credentials are referenced indirectly. A `passwordRef` of form `env:NAME` resolves from the
environment at load. The resolved value is stored but **never** logged.

### `src/telemetry/Logger`

Created in this unit despite the folder otherwise belonging to Unit 10 — logging is needed from
the first line of code.

Wrap spdlog behind a small interface so call sites do not depend on it directly. Rotating file
sink plus console sink, both using the exact format from `ui-context.md`. Path, rotation size,
and retention from config. Level from `logLevel`. Expose component-tagged helpers so a call site
names its tag once.

### `src/service`

`ServiceApp.h/.cpp` — the platform-independent orchestrator. In this unit: load config, validate,
init logger, log a configuration summary with credentials redacted, log version, transition to
`READY`, wait for shutdown, tear down in reverse order. Structure `start()` and `stop()` so later
units add subsystems without restructuring.

Owns `ServiceState` and a `transitionTo()` that logs old state, new state, and cause.

`main.cpp` — parse `--console`, `--config <path>`, `--version`, `--help`. Default config path is
`config/fcas.config.json` relative to the executable. No arguments prints usage and exits 2.
Ctrl+C handler requests graceful shutdown.

Exit codes: `0` clean, `1` runtime failure, `2` usage error, `3` configuration invalid.

### `config/fcas.config.json`

Commit a working default matching SRS §5.4, with known serial `DB0717739` mapped to `LEFT` and
two clearly-marked placeholder entries for `CENTER` and `RIGHT`.

### `tests/unit/config_test.cpp`

Cover: valid config loads; defaults merge and per-camera values override; duplicate serial
rejected; duplicate position rejected; invalid position string rejected; out-of-range port
rejected; invalid log level rejected; exposure above ceiling warns but still loads; multiple
simultaneous errors all reported; `env:` reference resolves and never appears in log output.

## Dependencies

- `nlohmann-json`, `spdlog`, `gtest` via vcpkg manifest

## Verify when done

- [ ] Solution opens in Visual Studio and builds Debug and Release x64 with no warnings
- [ ] `msbuild FabricInspection.sln /p:Configuration=Release /p:Platform=x64` succeeds from a clean tree
- [ ] Only x64 exists as a platform; Win32 is absent from the solution
- [ ] `Fcas.exe --version` prints the version and exits 0
- [ ] `Fcas.exe` with no arguments prints usage and exits 2
- [ ] `Fcas.exe --console` loads config, logs startup, reaches `READY`, exits 0 on Ctrl+C
- [ ] `Fcas.exe --console --config <bad file>` reports every invalid field by path and exits 3
- [ ] A config with a duplicate serial and an invalid position reports **both** errors in one run
- [ ] Log output matches `ui-context.md` exactly, including padding
- [ ] Rotating log file is created and rotates at the configured size
- [ ] No credential value appears in any log line or console output
- [ ] `FcasTests.exe` passes all tests
- [ ] No MVS SDK or AMQP header is referenced anywhere in this unit
- [ ] `.gitignore` excludes all build output; `git status` is clean after a build
- [ ] `context/progress-tracker.md` updated; committed as `feat(unit-01): solution skeleton, config, logging, console mode`
