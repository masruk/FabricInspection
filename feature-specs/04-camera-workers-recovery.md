# Unit 04: Camera Workers, Manager, Hot-Plug and Recovery

## Goal

Run one acquisition thread per camera, detect cameras appearing and disappearing at runtime, and
recover automatically from disconnection with exponential backoff. The service degrades when a
camera is lost and returns to normal when it comes back — without a restart.

## Design

This is the "camera connection recovery loop" from the team's architecture diagram.

One thread per camera gives fault isolation (`architecture.md` invariant 8): a hung or
disconnected camera blocks only its own thread. Two of three cameras still inspect two thirds of
the web, so losing one is `DEGRADED`, never `FAULT`.

Identity is by serial number throughout (invariant 4). A camera replugged into a different USB
port must return to the same logical position. This is the single most important behaviour in
this unit — getting it wrong silently swaps defect positions across the web after a reboot.

## Implementation

### `src/camera/CameraWorker`

One thread per camera. Loop body per the SDD:

```
getFrame(frame, 1000ms)
  timeout -> continue silently
  error   -> record, signal manager, backoff, continue
ScopedMvsFrame guard(device, frame)      // RAII release
lease = pool.acquire()
  empty -> count POOL_EXHAUSTED, continue
convertBayerToRgb8(frame, lease.data())
// guard releases the SDK buffer here
checkFrameCounterContinuity(frame.nFrameNum)
sink.submit(std::move(lease), captureMeta)
```

`captureMeta` carries position, serial, host timestamp, width, height, exposure, gain, and the
camera-local frame number.

The sink is an interface, not a concrete type — Unit 05 plugs in the correlator. In this unit a
simple counting sink is enough.

Frame counter continuity: a delta other than 1 means that camera missed a trigger. Log it, count
it per camera, and surface it in status. It is a **diagnostic only** and must never feed into any
grouping or correlation decision.

The thread body has a top-level `try/catch` that logs, marks the worker faulted, and returns
cleanly (invariant 6).

### `src/camera/CameraManager`

Owns the camera registry and every worker.

- Registry maps position → `{ serial, device, worker, state, lastError, backoff }`, guarded by a
  `std::shared_mutex` — reads are frequent (status), writes are rare (hot-plug).
- Startup: enumerate, open every mapped camera found, apply settings, start a worker for each.
- **Hot-plug polling** on a dedicated thread at the configured interval (default 3 s). Compare the
  current enumeration against the registry to detect arrivals and departures.
- **Arrival** of a configured serial: open, apply its settings profile, start its worker, log,
  update aggregate state (FR-104).
- **Departure**: stop and join the worker, close the device, mark `DISCONNECTED`, log, update
  aggregate state (FR-106).
- **Reconnect backoff**: exponential from 1 s to a 30 s cap, retried indefinitely (FR-107). Reset
  the backoff on a successful reconnect.
- Unmapped serials are logged once — not on every poll — and excluded.

Detecting departure: a disconnected camera may surface either as an SDK error on `getFrame` or as
absence from enumeration. Handle both, and make them converge on the same code path so recovery
behaves identically regardless of how the loss was noticed.

### Aggregate state

`CameraManager` computes the camera contribution to `ServiceState`:

| Condition | State |
| --- | --- |
| All mapped cameras connected, acquisition active | `RUNNING` |
| At least one but not all connected, acquisition active | `DEGRADED` |
| Zero mapped cameras connected | `FAULT` |
| Cameras open, acquisition stopped | `READY` |

Report state changes to `ServiceApp`, which owns the transition and logs old state, new state,
and cause.

### `src/service` wiring

`ServiceApp::start()` constructs the pool, then `CameraManager`. `stop()` tears down in reverse:
stop workers, join threads, close devices, release the pool, finalise the SDK. Shutdown must
complete within 10 s even with a camera mid-timeout.

Add `--run` to `main.cpp`: start acquisition in free-run and keep running until Ctrl+C, logging
state transitions and periodic frame counts. This is the mode used to verify hot-plug by hand.

### `tests/unit/camera_manager_test.cpp`

Hot-plug and recovery logic must be testable without physically unplugging anything. Structure
`CameraManager` so the enumeration source is injectable — a function returning a discovered list —
and drive the state machine from a fake.

Cover: all cameras present → `RUNNING`; one departs → `DEGRADED`; it returns → `RUNNING`; all
depart → `FAULT`; backoff grows to the cap and resets on success; a camera reappearing on a
different port keeps its logical position; an unmapped serial never enters the registry.

## Dependencies

None new.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] `Fcas.exe --console --run` starts a worker for the connected camera and reports frames
- [ ] Physically unplugging the camera moves the service to `DEGRADED` (or `FAULT` with only one
      camera) and logs the cause — no crash, no hang
- [ ] Replugging recovers to `RUNNING` within 30 s with no restart
- [ ] Replugging into a **different USB port** restores the same logical position
- [ ] Backoff intervals appear in the log and grow to the 30 s cap
- [ ] Ctrl+C during an active grab shuts down cleanly within 10 s
- [ ] Repeated unplug/replug cycles (at least 5) leak no handles and leave the pool at full size
- [ ] Frame counter gaps are logged as diagnostics and do not affect any other behaviour
- [ ] All unit tests pass, including Units 01–03
- [ ] Committed as `feat(unit-04): camera workers, manager, hot-plug and recovery`
