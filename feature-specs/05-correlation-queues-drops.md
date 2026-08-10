# Unit 05: Mock Camera, Trigger Correlation, Queues, Drop Accounting

## Goal

Group frames from separate cameras into trigger events, assign each event a shared `trigger_id`
and each camera a monotonic `sequence`, and hand the stamped images to bounded per-camera queues
with accounted drops. Introduce `MockCameraDevice` so three-camera behaviour is testable with one
physical camera.

## Design

This is the correctness heart of the system. With per-camera queues, `trigger_id` is the **only**
means the consumer has of reassembling a full-width slice. If correlation is wrong, defect
positions are wrong, and nothing downstream can detect it.

Correlation is by **host-timestamp window**, never by camera frame counters. See
`architecture.md` → Trigger Correlation Model for why: frame-counter correlation desynchronises
permanently and silently after a single missed trigger.

Images are stamped and enqueued **immediately**. The group exists only to allocate a consistent
`trigger_id` — nothing is buffered waiting for the set to complete. A missing camera simply
produces no message for that `trigger_id`.

## Implementation

### `tests/mocks/MockCameraDevice`

Implements `ICameraDevice` with no hardware. Must be able to:

- Produce synthetic frames on demand with a controllable host timestamp, so tests can simulate
  inter-camera skew precisely
- Simulate a missed trigger (produce no frame for one event)
- Simulate a frame counter gap
- Simulate connection loss and recovery
- Generate a recognisable test pattern so debayer output can be checked

Lives under `tests/` and is linked only by `FcasTests` — it must not ship in `Fcas.exe`.

Add a config option to run the service with N mock cameras alongside any real one, so a developer
with a single camera can exercise the full three-camera path end to end.

### `src/pipeline/TriggerCorrelator`

Owns one open group at a time. Runs on its own thread with a timed wait.

```
on image arrival (image i, host timestamp t):
    if no open group:
        open group; trigger_id = ++counter; groupStart = t
    else if (t - groupStart) > groupingWindowMs:
        close group
        open group; trigger_id = ++counter; groupStart = t
    else if group already holds i.position:
        close group                       // defensive: never merge two shots
        open group; trigger_id = ++counter; groupStart = t

    stamp i with trigger_id, ++sequence[i.position], position_mm, roll_id
    enqueue i to queue[i.position]

    if group holds all expected positions:
        close group                       // fast path
```

On the timed wait, force-close a group older than `groupingWindowMs`. At most one group is open,
so correlator memory is O(1).

`sequence` is per-camera, monotonic, and never reset except on service restart. `trigger_id` is
global and monotonic. Both are mandatory on every image (invariant 9).

Position accumulation: `position_mm += triggerPitchMm` per trigger event when
`acquisition.triggerPitchMm` is configured. `roll_id` comes from current state.

### `src/pipeline/BoundedQueue`

Fixed-capacity thread-safe queue, one instance per camera.

- Drop **oldest** on overflow (FR-509), because the newest fabric is always the most relevant
- Every drop invokes a callback so `DropAccountant` can record it
- Blocking pop with timeout for the consumer side
- Move-only element type — it holds `ImageLease`, which must not be copied

Per-camera instances mean a stalled publish for one camera cannot affect the others.

### `src/pipeline/DropAccountant`

Central record of every locally-known discard (FR-510).

- Counters per reason: `BROKER_UNAVAILABLE`, `LOCAL_QUEUE_FULL`, `POOL_EXHAUSTED`, `CAMERA_MISSING`
- Counters per camera position
- Records the `trigger_id` / `sequence` range of each contiguous run of drops
- Thread-safe with atomics on the hot path
- Exposes a snapshot for status and telemetry

Log each drop at `WARN` with `reason=`, `position=`, and `sequence=` per `ui-context.md`. Coalesce
repeated identical drops so a sustained outage does not flood the log — log the first, then a
periodic summary.

### Wiring

`CameraWorker`'s sink becomes `TriggerCorrelator`. A temporary drain thread pops from the queues
and releases leases so the pool recycles; Unit 06 replaces it with the real publisher.

Add `--run --mock-cameras 2` so the single real camera plus two mocks form a full three-camera set.

### `tests/unit/correlator_test.cpp`

The most important test file in the project. Cover:

- Three frames within the window get one shared `trigger_id`
- Frames beyond the window start a new group with a new `trigger_id`
- A missing camera produces a group with two images and no stall
- A duplicate position within one window force-closes and starts a new group
- `sequence` is monotonic per camera and independent across cameras
- `trigger_id` is monotonic and never reused
- Inter-camera skew up to the window boundary still groups correctly
- Skew beyond the boundary correctly splits — and the test documents this as the tuning limit
- A frame counter gap does not affect grouping
- `position_mm` accumulates correctly and resets on roll change
- Queue overflow drops the oldest and records the correct reason and range

## Dependencies

None new.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] `Fcas.exe --console --run --mock-cameras 2` produces groups of 3 with shared `trigger_id`
- [ ] Every image carries a `trigger_id` and a per-camera `sequence`; neither is ever zero or absent
- [ ] Simulating a missed frame on one mock yields a 2-image group and no stall or delay
- [ ] Simulating skew within the window still groups; beyond it splits as designed
- [ ] Queue overflow drops the oldest and `DropAccountant` records reason and range
- [ ] Sustained drops produce a periodic summary, not a per-frame log flood
- [ ] Pool returns to full size when the drain thread keeps up — no leak over 10 000 frames
- [ ] `MockCameraDevice` is not linked into `Fcas.exe`
- [ ] All unit tests pass, including Units 01–04
- [ ] Committed as `feat(unit-05): trigger correlation, queues, and drop accounting`
