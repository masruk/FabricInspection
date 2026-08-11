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

The algorithm is unchanged from the C++ design and must not drift during the port. If an
implementation detail here looks like a behaviour change, it is a defect.

## Implementation

### `tests/mocks/mock_camera.py`

`MockCameraDevice` satisfies the `ICameraDevice` Protocol structurally — no inheritance, no
`MvImport`, no hardware. Must be able to:

- Produce synthetic frames on demand with a **controllable host timestamp**, so tests can simulate
  inter-camera skew precisely
- Simulate a missed trigger (produce no frame for one event)
- Simulate a frame counter gap
- Simulate connection loss and recovery
- Generate a recognisable test pattern so debayer output can be checked

Lives under `tests/` and is importable only by tests. Add a test asserting nothing under
`src/fcas` imports it — the C++ version got this for free from the link step.

Add a config option to run the service with N mock cameras alongside any real one, so a developer
with a single camera can exercise the full three-camera path end to end. Wire it so the mock
implementation itself still lives in `tests/`; the service reads a flag and injects a factory.

### `src/fcas/pipeline/correlator.py`

Owns one open group at a time. Runs on its own thread with a `Condition.wait(timeout)`.

```python
def on_image(self, image: PendingImage) -> None:
    t = image.host_ts
    if self._group is None:
        self._open_group(t)
    elif (t - self._group.start) > self._window_ms:
        self._close_group()
        self._open_group(t)
    elif image.position in self._group.positions:
        self._close_group()                 # defensive: never merge two distinct shots
        self._open_group(t)

    self._group.positions.add(image.position)
    self._seq[image.position] += 1
    image.stamp(trigger_id=self._group.trigger_id,
                sequence=self._seq[image.position],
                position_mm=self._group.position_mm,
                roll_id=self._roll_id)
    self._queues[image.position].put_drop_oldest(image)

    if self._group.positions >= self._expected_positions:
        self._close_group()                 # fast path
```

On the timed wait, force-close a group older than `groupingWindowMs`. At most one group is open,
so correlator memory is O(1).

The timestamp is `MV_FRAME_OUT_INFO_EX.nHostTimeStamp` — the host stamp, not the device stamp,
because the device clocks are not synchronised to each other.

`sequence` is per-camera, monotonic, and never reset except on service restart. `trigger_id` is
global and monotonic. Both are mandatory on every image (invariant 9).

Position accumulation: `position_mm += triggerPitchMm` per trigger event when
`acquisition.triggerPitchMm` is configured. `roll_id` comes from current state.

The correlator's inbox is a **bounded** queue and its depth is monitored — workers must never
block on it. If the correlator ever falls behind, that is a drop with a reason, not backpressure
into acquisition.

### `src/fcas/pipeline/bounded_queue.py`

Fixed-capacity thread-safe queue, one instance per camera.

- Drop **oldest** on overflow (FR-509), because the newest fabric is always the most relevant
- Every drop invokes a callback so `DropAccountant` records it
- Blocking pop with timeout for the consumer side
- Holds `StampedImage` objects, each owning an `ImageLease`

**Do not use `collections.deque(maxlen=N)`.** A `maxlen` deque discards the evicted item silently,
and this queue's evicted item owns a 15 MB lease that must be closed and counted. Eviction is
explicit:

```python
def put_drop_oldest(self, item: StampedImage) -> None:
    with self._cv:
        if len(self._items) >= self._capacity:
            evicted = self._items.popleft()
            evicted.lease.close()                   # buffer back to the pool, immediately
            self._on_drop(DropReason.LOCAL_QUEUE_FULL, evicted)
        self._items.append(item)
        self._cv.notify()
```

Per-camera instances mean a stalled publish for one camera cannot affect the others.

### `src/fcas/pipeline/drops.py`

Central record of every locally-known discard (FR-510).

- Counters per reason: `BROKER_UNAVAILABLE`, `LOCAL_QUEUE_FULL`, `POOL_EXHAUSTED`, `CAMERA_MISSING`
- Counters per camera position
- Records the `trigger_id` / `sequence` range of each contiguous run of drops
- Thread-safe with a single short-held lock. `counter += 1` is **not** atomic in CPython, and at
  ~1 increment/s the lock costs nothing — do not try to be clever here.
- Exposes an immutable snapshot for status and telemetry

Log each drop at `WARN` with `reason=`, `position=`, and `sequence=` per `ui-context.md`. Coalesce
repeated identical drops so a sustained outage does not flood the log — log the first, then a
periodic summary.

### Wiring

`CameraWorker`'s sink becomes `TriggerCorrelator`. A temporary drain thread pops from the queues
and **closes the leases** so the pool recycles; Unit 06 replaces it with the real publisher. If
that drain forgets to close, the pool empties in seconds and every subsequent frame is
`POOL_EXHAUSTED` — which is exactly the failure the leak fixture is there to catch.

Add `--mock-cameras 2` to `fcas run --console` so the single real camera plus two mocks form a
full three-camera set.

### `tests/unit/test_correlator.py`

The most important test file in the project. Inject the clock; do not sleep. Cover:

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
- Queue overflow drops the oldest, closes its lease, and records the correct reason and range
- The pool is back to full size at the end of every case (the shared leak fixture)

### `tests/unit/test_bounded_queue.py`

Cover: capacity respected; oldest evicted first; the evicted lease is closed exactly once; the
drop callback fires with the right reason; concurrent put/pop from several threads leaks no
leases; pop with timeout returns cleanly when empty.

## Dependencies

None new.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] `fcas run --console --mock-cameras 2` produces groups of 3 with shared `trigger_id`
- [ ] Every image carries a `trigger_id` and a per-camera `sequence`; neither is ever zero or absent
- [ ] Simulating a missed frame on one mock yields a 2-image group and no stall or delay
- [ ] Simulating skew within the window still groups; beyond it splits as designed
- [ ] Queue overflow drops the oldest, closes its lease, and `DropAccountant` records reason and range
- [ ] Sustained drops produce a periodic summary, not a per-frame log flood
- [ ] Pool returns to full size when the drain thread keeps up — no leak over 10 000 frames
- [ ] `MockCameraDevice` is not importable from `src/fcas`, verified by a test
- [ ] No `deque(maxlen=...)` is used anywhere a lease could be silently discarded
- [ ] All tests pass, including Units 01–04
- [ ] Committed as `feat(unit-05): trigger correlation, queues, and drop accounting`
