# Unit 03: Capture, Debayer, and the Buffer Pool

## Goal

Capture frames from one camera, convert Bayer to RGB8 directly into a pre-allocated pooled buffer,
and write the result to disk for visual confirmation. Establish the memory model that makes 24/7
operation possible in a garbage-collected language.

## Design

This unit implements invariant 2 from `architecture.md`: **no large allocation occurs in steady
state, and no image is ever copied.** The pool is sized and allocated at startup and never grows.
Every later unit gets its image buffers from here.

It also implements invariant 5: every MVS buffer is released in the same `with` block that
acquired it, and every pooled lease is closed in a `finally`. Python has no destructors to fall
back on, so this is a discipline enforced by structure and by tests, not by the language.

Disk output exists solely to verify the image is correct. It is a diagnostic mode, off by default,
and never part of the production path.

## Implementation

### `src/fcas/pipeline/buffer_pool.py`

`ImageBuffer` — one `bytearray` sized to `width * height * 3` at construction, plus **two cached
views created once**:

```python
self._data        = bytearray(frame_bytes)
self._ctypes_view = (ctypes.c_ubyte * frame_bytes).from_buffer(self._data)  # SDK destination
self._view        = memoryview(self._data)                                  # publish body
```

`from_buffer` pins the `bytearray` so it can never be resized while the view exists — that is
exactly the invariant we want, and it is enforced by the interpreter rather than by convention.
Expose `ctypes_ptr` (cast to `POINTER(c_ubyte)`, for `MV_CC_ConvertPixelTypeEx.pDstBuffer`) and
`body(length) -> memoryview` (for Unit 06's publisher). Never expose the raw `bytearray`.

> `ctypes` is imported here, in the pipeline, purely to construct the view. That is a deliberate
> narrow exception to the "ctypes lives in `fcas/camera`" habit: the alternative is for the pool
> to hand out a bare `bytearray` and for the camera layer to build a view per frame, which
> allocates on the hot path. Document the exception in a module docstring so it is not mistaken
> for drift.

`ImageBufferPool` — thread-safe free list:

- Constructor allocates **all** buffers up front from `(count, frame_bytes)`. No lazy allocation.
- `acquire()` returns an `ImageLease`, or `None` when the pool is exhausted. Exhaustion is a
  normal condition to be counted and logged, never an exception and never a block-forever.
- `free_count` and `size` properties for status, tests, and the leak fixture.
- The lock is held only for a `deque` pop/append — never across a debayer or any I/O.

`ImageLease` — a context manager holding a buffer:

- `close()` returns the buffer to the pool and is idempotent.
- `__enter__` / `__exit__` so `with pool.acquire() as lease:` works.
- A `weakref.finalize` returns the buffer **and logs `ERROR pipeline lease leaked`** if it is
  collected without `close()`. This is a diagnostic net, not the mechanism: any lease that reaches
  it is a bug. Without it, a leak surfaces a week later as an unexplained `POOL_EXHAUSTED`.
- Accessing a closed lease raises. A use-after-close must fail loudly, not read a buffer another
  thread is writing.

### Memory budget self-check

At startup, compute and log the budget using the formula in `architecture.md`:

```
frame_bytes = width * height * 3
pool_bytes  = frame_bytes * pool_size
```

Log the computed total together with the estimated runtime overhead. If it exceeds
`service.maxMemoryBudgetMB`, log a fatal error and refuse to start (transition to `FAULT`). This is
a hard gate, not a warning — the vision box RAM is not confirmed and silently over-allocating on a
constrained box would fail in the field rather than on the bench.

Pool size comes from `acquisition.bufferPoolSize`, defaulting per the SDD:
`queue depths + workers + publisher + margin`.

Call `gc.freeze()` once, immediately after the pool is allocated and startup completes. Leave the
cyclic collector enabled.

### `src/fcas/camera/scoped_frame.py`

```python
@contextmanager
def scoped_frame(device: CameraDevice, frame: CapturedFrame) -> Iterator[CapturedFrame]:
    try:
        yield frame
    finally:
        device.free_frame(frame)      # MV_CC_FreeImageBuffer
```

Every `MV_CC_GetImageBuffer` call site uses it. There are no manual free calls anywhere.

### `src/fcas/camera/pixel_converter.py`

Wraps `MV_CC_ConvertPixelTypeEx` to convert a captured frame to RGB8.

- Fill `MV_CC_PIXEL_CONVERT_PARAM_EX` from the frame's `stFrameInfo`: `nWidth`, `nHeight`,
  `pSrcData`, `nSrcDataLen`, `enSrcPixelType`; destination `PixelType_Gvsp_RGB8_Packed`,
  `pDstBuffer = lease.ctypes_ptr`, `nDstBufferSize = lease.capacity`.
- **Debayer writes directly into the pooled buffer.** Do not allocate an intermediate buffer, do
  not `memmove` into a fresh `bytes`, do not round-trip through numpy. The samples copy the result
  out afterwards because they are writing a file; we are not.
- Bayer interpolation quality from config via `MV_CC_SetBayerCvtQuality`, defaulting to balanced.
- Validate `lease.capacity >= width * height * 3` **before** calling the SDK. A short buffer is a
  programming error and must fail loudly, not let a C function write past the end of a `bytearray`.
- Handles both mono and colour source formats, selecting the correct destination format and
  channel count.
- Return the actual output length (`nDstLen`) — the publisher needs it for the body slice.

### `src/fcas/camera/device.py` — settings and capture

Fill in the methods stubbed in Unit 02.

`apply_settings()`:

- Applies ROI (`Width`, `Height`, `OffsetX`, `OffsetY` via `MV_CC_SetIntValueEx`),
  `ExposureTime`, `Gain` (`MV_CC_SetFloatValue`), gamma, contrast, white balance
- **Validates every value against the camera's reported range** before applying (FR-205). Query
  `MV_CC_GetFloatValue` → `MVCC_FLOATVALUE(fCurValue, fMax, fMin)` and `MV_CC_GetIntValueEx` →
  `MVCC_INTVALUE_EX(..., nMax, nMin, nInc)`. Do not assume the configured value is legal, and
  respect `nInc` — a width that is not a multiple of the increment is silently adjusted or
  rejected by the camera depending on the model.
- Enforces the exposure ceiling (FR-206): a configured exposure above
  `acquisition.exposureCeilingUs` is clamped and logged as a warning
- Applies settings in an order the SDK accepts — width/height before offsets, since offsets are
  range-limited by the current ROI
- A single failed parameter does not abort the rest: collect `SettingFailure` entries and return
  them all

`start_grabbing()` / `stop_grabbing()` — wrap the SDK calls, guard against double-start.

`get_frame(timeout_ms)` — `MV_CC_GetImageBuffer`. **Returns `None` on timeout**; a timeout is not
an error, it is the normal condition when no trigger has fired. Any other non-zero return raises
`MvsError`.

### Free-run capture for verification

This unit has no trigger hardware, so verification uses free-run mode. Implement `fcas capture N`:
open the first mapped camera, set free-run at the configured FPS, capture N frames, debayer each
into a pooled buffer, write to disk, then stop and exit.

Write output with `MV_CC_SaveImageToFileEx2` from the SDK, to `service.diagnosticImageDir`.
Using the SDK's own writer avoids adding an imaging dependency for a diagnostic path. Filenames
include position, sequence, and timestamp.

### `tests/unit/test_buffer_pool.py`

Pool behaviour is pure logic and must be fully tested without hardware.

Cover: pool allocates exactly `count` buffers at construction; `acquire` returns distinct buffers;
exhaustion returns `None` rather than blocking or raising; a closed lease returns its buffer and it
can be acquired again; `close()` is idempotent; use-after-close raises; concurrent
acquire/close from multiple threads leaks nothing and returns the pool to full size; the leak
finalizer fires and logs when a lease is dropped without closing (force with `gc.collect()`).

### `tests/conftest.py`

Add the **pool-leak fixture** that every later pipeline test uses: at teardown, assert
`pool.free_count == pool.size`. In a language without destructors this is the cheapest possible
defence against the failure mode that would otherwise surface as a failed seven-day soak.

## Dependencies

None new.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] Startup logs the computed memory budget
- [ ] A configured budget below the computed requirement causes a clean `FAULT` and exit 3, not a
      crash or a `MemoryError`
- [ ] `fcas capture 20` writes 20 correct RGB8 images to the diagnostic directory
- [ ] Images open correctly and colours are right — not swapped channels, not a Bayer pattern
- [ ] Exposure configured above the ceiling is clamped and a warning is logged
- [ ] A configured value outside the camera's valid range is rejected with a clear message and the
      remaining settings still apply
- [ ] Capturing 500 frames shows no RSS growth after warm-up — confirm with Task Manager or
      `psutil` sampling
- [ ] `tracemalloc` over a 500-frame run shows no large allocation after the pool is built
- [ ] Pool exhaustion is counted and logged, and capture continues once buffers are returned
- [ ] Deliberately dropping a lease without closing produces the `lease leaked` ERROR line
- [ ] Frame timeout during idle produces no error-level log spam
- [ ] No image copy exists on the capture path — no `bytes(...)`, no `memmove` into a new buffer,
      no numpy round-trip
- [ ] All tests pass, including Units 01–02
- [ ] Committed as `feat(unit-03): capture, debayer, and buffer pool`
