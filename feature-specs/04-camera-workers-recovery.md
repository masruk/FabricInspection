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

The threads genuinely overlap despite the GIL: `MV_CC_GetImageBuffer` and
`MV_CC_ConvertPixelTypeEx` are `ctypes` calls through a `WinDLL` handle, which release the GIL for
their duration. See SDD §4.5 — if that turns out to be wrong, this is the unit where it becomes
visible, and the finding goes in `progress-tracker.md`, not into a workaround.

Identity is by serial number throughout (invariant 4). A camera replugged into a different USB
port must return to the same logical position. This is the single most important behaviour in
this unit — getting it wrong silently swaps defect positions across the web after a reboot.

## Implementation

### `src/fcas/camera/worker.py`

One thread per camera, created with an explicit `name=` so it is identifiable in a stack dump.
Loop body per the SDD:

```python
while not self._stop.is_set():
    frame = self._device.get_frame(timeout_ms=1000)
    if frame is None:
        continue                                    # normal when the line is idle
    with scoped_frame(self._device, frame):         # guarantees MV_CC_FreeImageBuffer
        lease = self._pool.acquire()
        if lease is None:
            self._drops.record(DropReason.POOL_EXHAUSTED, self._position)
            continue
        length = self._converter.to_rgb8(frame, lease)
    self._check_frame_counter(frame.frame_num)      # diagnostic only
    self._sink.submit(lease, self._capture_meta(frame, length))
```

On an SDK error rather than a timeout: record it, signal `CameraManager`, back off, continue.

`capture_meta` carries position, serial, host timestamp, width, height, exposure, gain, and the
camera-local frame number. Take **`nHostTimeStamp`, `fExposureTime` and `fGain` from
`MV_FRAME_OUT_INFO_EX`** — they describe *that* frame. Do not re-read exposure and gain from the
camera on the hot path; that is an extra SDK round-trip per frame and it reports the current
setting, not the one that produced the image.

The sink is a Protocol, not a concrete type — Unit 05 plugs in the correlator. In this unit a
simple counting sink is enough. It must close the lease, or the pool drains within seconds.

Frame counter continuity: a `nFrameNum` delta other than 1 means that camera missed a trigger. Log
it, count it per camera, and surface it in status. It is a **diagnostic only** and must never feed
into any grouping or correlation decision.

The thread body is wrapped in a top-level `try/except Exception` that logs, marks the worker
faulted, and returns cleanly (invariant 6). Without it an exception in a Python thread prints to
stderr and the thread dies silently — and under Session 0 there is no stderr, so the camera would
simply stop producing frames with no trace.

### `src/fcas/camera/manager.py`

Owns the camera registry and every worker.

- Registry maps position → `{serial, device, worker, state, last_error, backoff}`, guarded by a
  `threading.RLock`. Reads are frequent (status), writes are rare (hot-plug); the critical
  sections are microseconds of dict access, so a plain lock is correct and simpler than importing
  a reader-writer implementation.
- Startup: enumerate, open every mapped camera found, apply settings, start a worker for each.
- **Hot-plug polling** on a dedicated thread at `acquisition.hotplugPollIntervalMs` (default
  3000). Compare the current enumeration against the registry to detect arrivals and departures.
  The wait is `Event.wait(interval)`, never `time.sleep` — a stop request must abort it.
- **Arrival** of a configured serial: open, apply its settings profile, start its worker, log,
  update aggregate state (FR-104).
- **Departure**: signal the worker to stop, join it with a timeout, close the device, mark
  `DISCONNECTED`, log, update aggregate state (FR-106).
- **Reconnect backoff**: exponential from 1 s to a 30 s cap, retried indefinitely (FR-107). Reset
  the backoff on a successful reconnect. Wait on an `Event` so shutdown during a 30 s backoff still
  completes inside the 10 s budget.
- Unmapped serials are logged **once** — not on every poll — and excluded.

Detecting departure: a disconnected camera may surface either as an SDK error on `get_frame` or as
absence from enumeration. Handle both, and make them converge on the same code path so recovery
behaves identically regardless of how the loss was noticed.

Optionally register `MV_CC_RegisterExceptionCallBack` as a **latency optimisation only** — it
learns of a dead device sooner than the next poll. Its handler does nothing but set an `Event`:
no SDK calls, no allocation, no logging. It runs on an SDK-owned thread that has just re-entered
the interpreter, and anything more elaborate there is a source of deadlocks that only appear in
the field.

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

### Wiring

`ServiceApp.start()` constructs the pool, then `CameraManager`. `stop()` tears down in reverse:
signal workers, join threads with timeout, close devices, release the pool, finalise the SDK.
Shutdown must complete within 10 s even with a camera mid-timeout — which is why `get_frame` uses
a 1 s timeout rather than a longer one.

Implement `fcas run --console` to start acquisition in free-run and keep running until Ctrl+C,
logging state transitions and periodic frame counts. This is the mode used to verify hot-plug by
hand.

### `tests/unit/test_camera_manager.py`

Hot-plug and recovery logic must be testable without physically unplugging anything. Structure
`CameraManager` so the enumeration source is injectable — a callable returning a discovered list —
and drive the state machine from a fake. Inject the clock so backoff is tested without sleeping.

Cover: all cameras present → `RUNNING`; one departs → `DEGRADED`; it returns → `RUNNING`; all
depart → `FAULT`; backoff grows 1, 2, 4, 8, 16, 30, 30 and resets on success; a camera reappearing
at a different enumeration index keeps its logical position; an unmapped serial never enters the
registry; a stop request during backoff returns promptly.

### `tests/unit/test_worker.py`

Cover: a timeout produces no log and no drop; an SDK error is recorded and the loop continues;
pool exhaustion records `POOL_EXHAUSTED` and continues; a frame counter gap is logged and does not
affect anything else; an exception raised by the sink is caught by the thread guard and marks the
worker faulted rather than killing the thread silently; the pool returns to full size after the
worker stops.

## Dependencies

None new.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] `fcas run --console` starts a worker for the connected camera and reports frames
- [ ] Physically unplugging the camera moves the service to `DEGRADED` (or `FAULT` with only one
      camera) and logs the cause — no crash, no hang
- [ ] Replugging recovers to `RUNNING` within 30 s with no restart
- [ ] Replugging into a **different USB port** restores the same logical position
- [ ] Backoff intervals appear in the log and grow to the 30 s cap
- [ ] Ctrl+C during an active grab shuts down cleanly within 10 s, including during a backoff wait
- [ ] All threads are joined at shutdown — assert with `threading.enumerate()` after `stop()`
- [ ] Repeated unplug/replug cycles (at least 5) leak no handles and leave the pool at full size
- [ ] Frame counter gaps are logged as diagnostics and do not affect any other behaviour
- [ ] An exception injected into the sink does not kill the worker thread silently
- [ ] Two workers running concurrently show wall-clock overlap in their debayer timings —
      confirms the GIL analysis in SDD §4.5 (use one real camera plus a mock, or timing logs)
- [ ] `time.sleep` appears nowhere in `src/fcas`
- [ ] All tests pass, including Units 01–03
- [ ] Committed as `feat(unit-04): camera workers, manager, hot-plug and recovery`
