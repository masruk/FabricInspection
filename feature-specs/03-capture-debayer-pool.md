# Unit 03: Capture, Debayer, and the Buffer Pool

## Goal

Capture frames from one camera, convert Bayer to RGB8 directly into a pre-allocated pooled buffer,
and write the result to disk for visual confirmation. Establish the memory model that makes 24/7
operation possible.

## Design

This unit implements invariant 2 from `architecture.md`: **no large heap allocation occurs in
steady state.** The pool is sized and allocated at startup and never grows. Every later unit gets
its image buffers from here.

It also implements invariant 5: every MVS buffer acquired is released in the same scope via RAII.
The SDK buffer is held only long enough to debayer out of it.

Disk output exists solely to verify the image is correct. It is a diagnostic mode, off by default,
and never part of the production path.

## Implementation

### `src/pipeline/ImageBuffer` and `ImageBufferPool`

`ImageBuffer` — fixed-capacity byte buffer sized at construction to `width * height * 3`. Never
resized after construction. Exposes `data()` and `capacity()`.

`ImageBufferPool` — thread-safe free-list:

- Constructor allocates **all** buffers up front from `(count, frameBytes)`. No lazy allocation.
- `acquire()` returns an `ImageLease`, or an empty lease when the pool is exhausted. Exhaustion is
  a normal condition to be counted and logged, never an exception or a crash.
- `release()` returns a buffer to the free-list.
- The mutex is held only for the pop/push of a pointer — never across a debayer or any I/O.

`ImageLease` — move-only RAII handle returning its buffer on destruction. Not copyable. An early
return or a thrown exception must not leak a 15 MB buffer.

### Memory budget self-check

At startup, compute and log the budget using the formula in `architecture.md`:

```
frame_bytes = width * height * 3
pool_bytes  = frame_bytes * pool_size
```

Log the computed total. If it exceeds `service.maxMemoryBudgetMB` from config, log a fatal error
and refuse to start (transition to `FAULT`). This is a hard gate, not a warning — the vision box
RAM is not yet confirmed and silently over-allocating on a constrained box would fail in the field
rather than on the bench.

Pool size comes from config, defaulting per the SDD: `queue depths + workers + publisher + margin`.

### `src/camera/ScopedMvsFrame`

RAII guard binding an `MV_FRAME_OUT` to its device. Calls `MV_CC_FreeImageBuffer` on destruction.
Every `MV_CC_GetImageBuffer` call site uses it — there are no manual free calls anywhere.

### `src/camera/PixelConverter`

Wraps `MV_CC_ConvertPixelTypeEx` to convert a captured frame to RGB8.

- Destination is the caller-supplied pooled buffer. **Debayer writes directly into it** — do not
  allocate an intermediate buffer and copy.
- Bayer interpolation quality from config (`bayerQuality`), defaulting to balanced.
- Validates the destination is large enough before calling the SDK; a short buffer is a
  programming error and must fail loudly, not corrupt memory.
- Handles both mono and colour source formats, selecting the correct destination format and
  channel count.

### `src/camera/CameraDevice` — settings and capture

Fill in the methods stubbed in Unit 02.

`applySettings()`:

- Applies ROI (`Width`, `Height`, `OffsetX`, `OffsetY`), `ExposureTime`, `Gain`, gamma, contrast,
  white balance
- **Validates every value against the camera's reported range** before applying (FR-205). Query
  the node range via the SDK; do not assume the configured value is legal.
- Enforces the exposure ceiling (FR-206): a configured exposure above
  `acquisition.exposureCeilingUs` is clamped and logged as a warning
- Applies settings in an order the SDK accepts — ROI before offsets where required
- A single failed parameter does not abort the rest; collect failures and report them together

`startGrabbing()` / `stopGrabbing()` — wrap the SDK calls, guard against double-start.

`getFrame()` — `MV_CC_GetImageBuffer` with timeout. A timeout is **not** an error; it is the normal
condition when no trigger has fired. Return a distinct status so callers can continue silently.

### Free-run capture for verification

This unit has no trigger hardware, so verification uses free-run mode. Add `--capture N` to
`main.cpp`: open the first mapped camera, set free-run at the configured FPS, capture N frames,
debayer each into a pooled buffer, write to disk, then stop and exit.

Write output as BMP via `MV_CC_SaveImageToFileEx` (already proven working in the reference app),
to a configurable diagnostic directory. Filenames include position, sequence, and timestamp.

### `tests/unit/pool_test.cpp`

Pool behaviour is pure logic and must be fully tested without hardware.

Cover: pool allocates exactly `count` buffers at construction; `acquire` returns distinct buffers;
exhaustion returns an empty lease rather than blocking forever or throwing; a destroyed lease
returns its buffer and it can be acquired again; a moved-from lease does not double-release;
concurrent acquire/release from multiple threads leaks nothing and returns the pool to full size.

## Dependencies

None new.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] Startup logs the computed memory budget
- [ ] A configured budget below the computed requirement causes a clean `FAULT` and exit 3, not a crash
- [ ] `Fcas.exe --console --capture 20` writes 20 correct RGB8 images to the diagnostic directory
- [ ] Images open correctly and colours are right — not swapped channels, not a Bayer pattern
- [ ] Exposure configured above the ceiling is clamped and a warning is logged
- [ ] A configured value outside the camera's valid range is rejected with a clear message and the
      remaining settings still apply
- [ ] Capturing 500 frames shows no memory growth after warm-up — confirm in Task Manager or a profiler
- [ ] Pool exhaustion is counted and logged, and capture continues once buffers are returned
- [ ] Frame timeout during idle produces no error-level log spam
- [ ] All unit tests pass, including Units 01–02
- [ ] Committed as `feat(unit-03): capture, debayer, and buffer pool`
