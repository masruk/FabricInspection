# Unit 08: REST Control Server and `fcasctl`

## Goal

Expose the local REST API and build the `fcasctl` CLI on top of it, so an operator can inspect
status, change camera settings, control acquisition, and set the roll ID — all without restarting
the service.

## Design

This is the "config from UI/CLI" requirement in the team's architecture diagram.

The REST layer is a transport only: validate, delegate, return. **No business logic lives in a
handler** — that belongs in `CameraManager`, `ServiceApp`, or the pipeline.

`fcasctl` is a thin client. It contains no logic of its own, so there is exactly one
implementation of every operation and the CLI can never drift from the API.

Every response uses the single envelope defined in `ui-context.md`. Do not invent alternative
shapes per endpoint.

## Implementation

### `src/control/RestControlServer`

Embedded HTTP server on the configured address and port.

- Binds to `service.restListenAddress`, default `127.0.0.1` — **never** `0.0.0.0` by default
  (FR-605, NFR-404). Binding to all interfaces must be a deliberate config change.
- Runs on its own small thread pool, separate from acquisition
- Graceful shutdown within the service's 10 s budget
- Requests and responses are JSON throughout

### Endpoints

All under `/api/v1`, exactly as listed in `ui-context.md`:

| Method | Path | Behaviour |
| --- | --- | --- |
| `GET` | `/cameras` | List all configured cameras with state, serial, model, sequence, last error |
| `GET` | `/cameras/{position}/config` | Current effective settings for that camera |
| `PUT` | `/cameras/{position}/config` | Apply settings at runtime; partial updates allowed |
| `POST` | `/acquisition/start` | Start acquisition |
| `POST` | `/acquisition/stop` | Stop acquisition |
| `POST` | `/acquisition/trigger` | Software trigger to all active cameras |
| `POST` | `/roll` | Set roll ID and reset position accumulator |
| `GET` | `/status` | Full system status |
| `GET` | `/health` | Liveness and readiness |

Rules:

- `PUT .../config` validates against the camera's reported range before applying (FR-205, FR-207)
  and enforces the exposure ceiling. Invalid values return `400` with the offending field named,
  and **nothing** is applied — the update is all-or-nothing.
- Settings changes take effect without a service restart. Note in the response whether a change
  required a grab restart.
- `POST /acquisition/trigger` returns `409` when trigger mode is `HARDWARE` — software trigger is
  a commissioning tool, not a production path.
- Operations are idempotent where sensible: starting an already-running acquisition returns `200`,
  not an error (FR-604).
- Unknown position returns `404`.

HTTP status mapping per `ui-context.md`: `200` success, `400` invalid input, `404` unknown
resource, `409` invalid state, `500` internal failure. The envelope's `code` carries the numeric
FCAS error code; `0` means success.

### Status payload

`GET /status` returns everything in FR-701: state, version, uptime, roll ID, broker connection
state, total triggers, published, dropped by reason, achieved trigger rate, and a per-camera array
with position, serial, state, sequence, temperature, last error.

**Use one serializer for both `/status` and the telemetry message** (Unit 10) so the two can never
diverge.

### `tools/fcasctl`

Separate `FcasCtl.exe` project. Commands exactly as in `ui-context.md`:

```
fcasctl status
fcasctl cameras
fcasctl config get LEFT
fcasctl config set LEFT --exposure-us 700 --gain-db 6.0
fcasctl start | stop | trigger
fcasctl roll set R-2026-0801-17
```

Output rules:

- Default is a human-readable aligned table matching the `fcasctl status` example in `ui-context.md`
- `--json` on any command emits the raw envelope for scripting
- `--host` and `--port` override the default endpoint
- Exit `0` success, `1` operation failure, `2` usage error
- Errors go to **stderr** as `error: <message>`, never stdout
- No colour, no spinners, no progress bars — output may be captured to a log

### `tests/unit/api_test.cpp`

Handler logic is testable without a live socket if handlers are separated from routing.

Cover: envelope shape for success and failure; status-code mapping per error class; partial config
update applies only named fields; invalid value rejects the whole update; unknown position yields
`404`; software trigger in hardware mode yields `409`; idempotent start returns success.

### `tests/integration/rest_test.cpp`

Cover: server binds to the configured address only; each endpoint round-trips; a settings change
applied via `PUT` is visible in a subsequent `GET`; `fcasctl --json` output parses as the envelope.

## Dependencies

- `cpp-httplib` via vcpkg — add in this unit
- `nlohmann-json` — already present from Unit 01

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] Server binds to `127.0.0.1` by default and is **not** reachable from another machine
- [ ] Every endpoint returns the standard envelope, success and failure alike
- [ ] `fcasctl status` prints the aligned table from `ui-context.md`
- [ ] `fcasctl status --json` emits a valid parseable envelope
- [ ] `fcasctl config set LEFT --exposure-us 700` takes effect with no restart, confirmed by a
      subsequent `config get`
- [ ] An out-of-range exposure is rejected with `400`, names the field, and changes nothing
- [ ] An exposure above the ceiling is clamped and reported in the response
- [ ] `fcasctl trigger` returns `409` while trigger mode is `HARDWARE`
- [ ] `fcasctl roll set R-TEST` resets the position accumulator to zero
- [ ] Unknown position returns `404`; malformed JSON returns `400`
- [ ] Errors print to stderr; stdout stays clean when a command fails
- [ ] Exit codes are correct for success, failure, and usage error
- [ ] REST requests during active acquisition do not stall or drop frames
- [ ] All unit tests pass, including Units 01–07
- [ ] Committed as `feat(unit-08): REST control server and fcasctl CLI`
