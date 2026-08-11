# Unit 09: Windows Service Integration and Deployment

## Goal

Host the existing application as a Windows Service that starts automatically on boot without an
operator login, stops gracefully, restarts itself after a crash, and writes lifecycle events to
the Windows Event Log. Console mode continues to work unchanged.

## Design

Console mode has been the development loop for eight units and stays that way. This unit **wraps**
`ServiceApp`; it does not modify it. If service hosting requires a change to `ServiceApp`, the
boundary is wrong — fix the boundary rather than leaking SCM concerns downward.

Session 0 isolation is absolute: no window, no console, no message box, no desktop interaction,
ever (CON-002, OP-103). There is also **no stdout** — a stray `print()` or an unhandled exception
writing to stderr simply vanishes.

Running a Python service under the SCM has a specific set of ways to fail, and all of them look
like "the service starts and immediately stops" from `services.msc`. This unit exists mostly to
get those right once:

- The interpreter must be the venv's, resolved absolutely, not whatever `PATH` finds.
- The working directory is `%SystemRoot%\System32`, not the package directory.
- `MVCAM_COMMON_RUNENV` must be **machine-scope** or the MVS binding will not import.
- The MVS runtime DLL directory must be on the **machine** `PATH` for the same reason.
- Anything that fails before logging is configured leaves no trace anywhere but the Event Log.

## Implementation

### `src/fcas/service/windows_service.py`

`pywin32` SCM integration — the only module that imports `win32serviceutil`, `win32service`,
`servicemanager`.

- A `win32serviceutil.ServiceFramework` subclass with `_svc_name_`, `_svc_display_name_`,
  `_svc_description_`
- `SvcDoRun` constructs and starts `ServiceApp`, then waits on a stop event
- `SvcStop` sets the stop event and calls `ReportServiceStatus(SERVICE_STOP_PENDING)`
- Report `SERVICE_START_PENDING` with **incrementing checkpoints and a realistic `waitHint`**
  during initialisation — camera enumeration and opening take seconds, and SCM must not time out
- Accept stop and shutdown controls
- On stop: request graceful shutdown, complete within **10 s** (OP-104), report stopped with an
  accurate exit code
- On a fatal startup error, report stopped with a **non-zero exit code** so SCM recovery actions
  trigger correctly, and write the reason to the Event Log before exiting

**Bootstrap ordering matters.** Set the working directory to the package directory and configure
logging as the very first actions in `SvcDoRun`, inside a `try/except` that writes any failure to
the Event Log via `servicemanager.LogErrorMsg`. Everything before logging exists is invisible
otherwise.

### Install and uninstall

Implement in `fcas/__main__.py`:

```
fcas install [--account <name>] [--config <path>]
fcas uninstall
fcas run --console        (unchanged)
```

`install` registers the service with:

- The **absolute path to the venv interpreter** and the module to run. Do not rely on `PATH`.
- Start type auto-start with **delayed start** — USB enumeration must settle before the first
  camera scan (OP-101). Set with `win32service.ChangeServiceConfig2` /
  `SERVICE_CONFIG_DELAYED_AUTO_START_INFO`.
- Failure actions: restart after 5 s on first failure, 10 s on second, 30 s on subsequent, with a
  24 h reset period (OP-105, NFR-204), via `SERVICE_CONFIG_FAILURE_ACTIONS`.
- A description string.
- The configured service account, defaulting to `LocalSystem` with a note that a dedicated
  least-privilege account is the production requirement (NFR-403).

Both modes require elevation. Detect a non-elevated invocation
(`ctypes.windll.shell32.IsUserAnAdmin()`) and fail with a clear message rather than an opaque
access-denied.

**Preflight check on install.** Before registering, verify and report:

- The interpreter is 64-bit
- `MVCAM_COMMON_RUNENV` is set at **machine** scope, not merely in the current process
- The MVS binding directory exists at that path
- The config file resolves and validates

A failure found here is a two-line message. The same failure found after registration is a service
that starts and stops with event ID 7034 and no explanation.

### Event Log

Register an event source at install time. Write these events (FR-703):

| Event | Level |
| --- | --- |
| Service started, with version | Information |
| Service stopped, with reason | Information |
| Configuration invalid, refusing to start | Error |
| All cameras lost — `FAULT` | Error |
| Camera lost / recovered | Warning / Information |
| Broker lost / recovered | Warning / Information |
| Watchdog recovery triggered | Warning |

Implement as a `logging.Handler` that forwards records above a threshold and tagged as lifecycle
events to `servicemanager`, so call sites log once and both sinks are fed.

Event Log carries **lifecycle events only**. Operational detail stays in the rotating file log —
do not duplicate the whole log stream into the Event Log.

### Working directory and paths

A service starts with a working directory of `%SystemRoot%\System32`. Every relative path — config,
logs, diagnostics — must resolve against the package installation directory via
`fcas/common/paths.py` from Unit 01, never `os.getcwd()`. This is the single most common cause of
"works in console, fails as a service".

### Logging under Session 0

The console handler is attached only in console mode; under the SCM the file handler and the Event
Log handler are the only sinks. Confirm `print()` appears nowhere in `src/`.

Also set `sys.stdout`/`sys.stderr` to a null or logging-backed stream under the SCM, so a
third-party library that writes to them cannot raise on a missing handle.

### `Documents/deployment-guide.md`

Written so someone can provision a fresh vision box without asking questions (OP-109):

1. Install CPython 3.12 x64 to a fixed path, for all users
2. Create the venv at the fixed deployment path
3. `pip install --no-index --find-links <wheelhouse> -r requirements.lock` — offline install
4. Install the FCAS package
5. Set `MVCAM_COMMON_RUNENV` at machine scope if not already; confirm the MVS runtime DLL
   directory is on the machine `PATH`
6. Place and edit `config/fcas.config.json`; set the broker password environment variable at
   machine scope
7. `fcas install --account <svc account>` from an elevated prompt
8. Verify with `fcasctl status`, then reboot and verify again
9. Upgrade procedure: stop service, install new package version, start service — and how to roll
   back

### `tests/unit/test_service_paths.py`

Cover: a relative config path resolves against the package directory, not the CWD (change the CWD
in the test); absolute paths pass through unchanged; the log path resolves identically;
hosting-mode detection selects the right handler set.

## Dependencies

- `pywin32` — add in this unit

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] `fcas install` from an elevated prompt registers the service; non-elevated fails with a
      clear message
- [ ] The install preflight catches a missing/user-scope `MVCAM_COMMON_RUNENV` **before**
      registering, with an actionable message
- [ ] The registered image path points at the venv interpreter absolutely, not a `PATH` lookup
- [ ] The service appears in `services.msc` with delayed automatic start and the configured
      recovery actions
- [ ] `net start Fcas` starts it; it reaches `READY` with the camera connected
- [ ] **Reboot the machine — the service starts with no login and reaches `READY`** (AC-01)
- [ ] Repeat the reboot test 5 times with consistent results
- [ ] `net stop Fcas` shuts down gracefully within 10 s
- [ ] Killing the process via Task Manager triggers automatic restart per the recovery policy
- [ ] Config, log, and diagnostic paths resolve correctly under the service — **not** to `System32`
- [ ] Event Log shows start, stop, and fault entries under the registered source
- [ ] A deliberately broken `MVCAM_COMMON_RUNENV` produces a readable Event Log entry, not a silent
      immediate stop
- [ ] No console window, message box, or desktop interaction appears at any point
- [ ] `fcas run --console` still works exactly as before
- [ ] `fcasctl status` works against the running service
- [ ] `fcas uninstall` removes the service and its event source cleanly
- [ ] Starting with an invalid config logs to the Event Log and reports a non-zero exit to SCM
- [ ] `Documents/deployment-guide.md` is complete enough that a fresh box can be provisioned from
      it without asking a question
- [ ] All tests pass, including Units 01–08
- [ ] Committed as `feat(unit-09): Windows Service integration and deployment`
