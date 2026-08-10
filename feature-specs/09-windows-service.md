# Unit 09: Windows Service Integration

## Goal

Host the existing application as a Windows Service that starts automatically on boot without an
operator login, stops gracefully, restarts itself after a crash, and writes lifecycle events to
the Windows Event Log. Console mode continues to work unchanged.

## Design

Console mode has been the development loop for eight units and stays that way. This unit **wraps**
`ServiceApp`; it does not modify it. If service hosting requires a change to `ServiceApp`, the
boundary is wrong — fix the boundary rather than leaking SCM concerns downward.

Session 0 isolation is absolute: no window, no console, no message box, no desktop interaction,
ever (CON-002, OP-103). A single `MessageBox` in an error path would hang the service invisibly on
a headless box.

## Implementation

### `src/service/WindowsService`

SCM integration, the only place in the codebase that calls SCM APIs.

- `ServiceMain` and `RegisterServiceCtrlHandlerEx`
- Report `SERVICE_START_PENDING` with incrementing checkpoints and a realistic wait hint during
  initialisation — camera enumeration and opening take seconds, and SCM must not time out
- Accept `SERVICE_CONTROL_STOP` and `SERVICE_CONTROL_SHUTDOWN`
- On stop: request graceful shutdown, complete within **10 s** (OP-104), report `SERVICE_STOPPED`
  with an accurate exit code
- On a fatal startup error, report `SERVICE_STOPPED` with a non-zero `dwWin32ExitCode` so SCM
  recovery actions trigger correctly

### Install and uninstall

Add `main.cpp` modes:

```
Fcas.exe --install     [--account <name>] [--config <path>]
Fcas.exe --uninstall
Fcas.exe --console     (unchanged)
```

`--install` registers the service with:

- Start type `SERVICE_AUTO_START` with **delayed start** — USB enumeration must settle before the
  first camera scan (OP-101)
- Failure actions: restart after 5 s on first failure, 10 s on second, 30 s on subsequent, with a
  24 h reset period (OP-105, NFR-204)
- A description string
- The configured service account, defaulting to `LocalSystem` with a note that a dedicated
  least-privilege account is the production requirement (NFR-403)

Both modes require elevation. Detect a non-elevated invocation and fail with a clear message
rather than an opaque access-denied.

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

Event Log carries **lifecycle events only**. Operational detail stays in the rotating file log —
do not duplicate the whole log stream into the Event Log.

### Working directory and paths

A service starts with a working directory of `%SystemRoot%\System32`, not the executable's folder.
Every relative path — config, logs, diagnostics — must resolve against the executable's directory,
not the process working directory. This is the single most common cause of "works in console,
fails as a service".

### Logging under Session 0

The console sink must be disabled when running as a service. Detect the hosting mode and configure
sinks accordingly — file only under SCM, file plus console under `--console`.

### `tests/unit/service_paths_test.cpp`

Cover: relative config path resolves against the executable directory, not the CWD; absolute paths
pass through unchanged; the log path resolves identically; hosting-mode detection selects the
right sink set.

## Dependencies

None new. Uses the Win32 service APIs already available.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] `Fcas.exe --install` from an elevated prompt registers the service; non-elevated fails with a clear message
- [ ] The service appears in `services.msc` with delayed automatic start and the configured recovery actions
- [ ] `net start Fcas` starts it; it reaches `READY` with the camera connected
- [ ] **Reboot the machine — the service starts with no login and reaches `READY`** (AC-01)
- [ ] Repeat the reboot test 5 times with consistent results
- [ ] `net stop Fcas` shuts down gracefully within 10 s
- [ ] Killing the process via Task Manager triggers automatic restart per the recovery policy
- [ ] Config, log, and diagnostic paths resolve correctly under the service — **not** to `System32`
- [ ] Event Log shows start, stop, and fault entries under the registered source
- [ ] No console window, message box, or desktop interaction appears at any point
- [ ] `Fcas.exe --console` still works exactly as before
- [ ] `fcasctl status` works against the running service
- [ ] `Fcas.exe --uninstall` removes the service and its event source cleanly
- [ ] Starting with an invalid config logs to the Event Log and reports a non-zero exit to SCM
- [ ] All unit tests pass, including Units 01–08
- [ ] Committed as `feat(unit-09): Windows Service integration`
