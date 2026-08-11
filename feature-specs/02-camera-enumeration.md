# Unit 02: MVS SDK Wrapper and Camera Enumeration

## Goal

Introduce the Hikrobot MVS Python SDK behind a single typed wrapper module, enumerate connected
USB3 cameras, and map each discovered serial number to its configured logical position. No image
capture in this unit — enumerate, open, close.

## Design

This unit establishes the boundary rule that holds for the rest of the project: **`MvImport` is
imported in exactly one module, `fcas/camera/mvs_sdk.py`.** No other module ever sees an `MV_CC_*`
symbol or a `ctypes` structure.

In C++ that boundary was enforced by the include graph. Here nothing enforces it but us, so it is
enforced twice — by the ruff banned-imports rule configured in Unit 01, and by a test that walks
the AST of every module.

`ICameraDevice` is introduced here rather than later because every subsequent unit depends on
being able to substitute a mock — only one physical camera exists during development. It is a
`typing.Protocol`, so `MockCameraDevice` satisfies it structurally without inheriting anything.

**Reference material** (read-only; never modify anything under the MVS install):

- `…\MVS\Development\Samples\Python\General\GrabImage\GrabImage.py` — the validated
  initialize → enumerate → create handle → open → close → destroy → finalize sequence
- `…\MVS\Development\Samples\Python\MvImport\MvCameraControl_class.py` — the binding itself
- The `.chm` developer guide for anything the samples do not show

Follow the samples for *call sequence and struct usage*. Do not copy their structure — they are
flat scripts with `sys.exit()` in the middle; this is a layered service.

## Implementation

### `src/fcas/camera/mvs_sdk.py` — the single SDK boundary

Everything the rest of the codebase needs from the SDK passes through here.

**Path resolution.** Append `os.environ["MVCAM_COMMON_RUNENV"]/Samples/Python/MvImport` to
`sys.path` before importing. If the variable is unset or the directory does not exist, raise a
`ConfigError` naming the variable and the expected path. Do not let this surface as a raw
`ImportError` from a vendor file — under a service account this is the single most likely
first-run failure (SRS open item 12).

**The star import.** Perform the vendor's `from MvCameraControl_class import *` here, once, and
re-export by explicit `__all__` only the names the codebase uses. This is the only star-import
permitted in the project.

**SDK lifetime.** `MvsSdk` as a context manager over `MV_CC_Initialize` / `MV_CC_Finalize`, with
an explicit `init()`; never initialise in a constructor. `Finalize` runs exactly once at shutdown,
including on the error path. Exposes the SDK version string (`MV_CC_GetSDKVersion`, format as hex)
for logging at startup.

**Error mapping.** `check(ret: int, op: str) -> None` raises `MvsError` carrying the raw return as
`sdk_ret` and formatting it as hex in the message. Map the common codes from
`MvErrorDefine_const.py` to readable text where it helps diagnosis. **No call site anywhere else
in the codebase inspects a raw return value.**

**String decoding.** `decode_vendor_str(char_array) -> str` — truncate at the first NUL, then try
`gbk`, `utf-8`, `latin-1` in turn, falling back to `latin-1` with replacement. This is the
vendor's own pattern; these fields are neither guaranteed UTF-8 nor guaranteed NUL-terminated.

### `src/fcas/camera/interface.py`

The `ICameraDevice` Protocol, declaring the complete lifecycle later units need so mocks and real
devices stay in step:

```python
class ICameraDevice(Protocol):
    def open(self, info: DiscoveredCamera) -> None: ...
    def apply_settings(self, s: CameraSettings) -> list[SettingFailure]: ...   # Unit 03
    def configure_trigger(self, t: TriggerConfig) -> None: ...                 # Unit 12
    def start_grabbing(self) -> None: ...                                      # Unit 03
    def stop_grabbing(self) -> None: ...                                       # Unit 03
    def get_frame(self, timeout_ms: int) -> CapturedFrame | None: ...          # Unit 03
    def close(self) -> None: ...
    @property
    def serial(self) -> str: ...
    @property
    def model_name(self) -> str: ...
    @property
    def position(self) -> CameraPosition: ...
    @property
    def is_connected(self) -> bool: ...
```

Methods not implemented in this unit raise `MvsError(E_CAM_NOT_IMPLEMENTED)`. They are filled in
by Units 03, 04 and 12.

### `src/fcas/camera/device.py`

`CameraDevice` implements the Protocol over the MVS binding.

- Owns an `MvCamera` instance and its handle. It is a **context manager**: `__exit__` calls
  `MV_CC_CloseDevice` then `MV_CC_DestroyHandle`, unconditionally. There must be no code path
  that leaks a handle.
- `open()` performs `MV_CC_CreateHandle` then `MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)`, mapping
  every failure through `check()` so the raw hex code is preserved.
- Reads model name and serial from the USB3 branch of `MV_CC_DEVICE_INFO`
  (`SpecialInfo.stUsb3VInfo`), decoded with `decode_vendor_str`.

### `src/fcas/camera/enumerator.py`

- Wraps `MV_CC_EnumDevices` for `MV_USB_DEVICE` only. GigE and GenTL transports are out of scope
  and must not be enumerated.
- Returns a list of plain `DiscoveredCamera` dataclasses (serial, model, index) — **no ctypes
  type escapes this module.**
- Resolves each discovered serial against the configured camera list, producing a mapped position
  or `UNMAPPED`.
- Logs unmapped serials as a warning and excludes them from any further use (FR-105).
- Reports configured cameras that were not discovered as missing.

Keep the resolution step as a **pure function** taking a discovered list plus a configured list
and returning the resolution. That is what makes it testable without hardware.

### Wiring

Extend `ServiceApp.start()` to initialise `MvsSdk` after config validation and log the SDK
version alongside the interpreter version. Extend teardown to finalise it last.

Implement `fcas list-cameras`: enumerate, print the table, exit without entering the run loop.

```
POSITION  SERIAL      MODEL             STATE
LEFT      DB0717739   MV-CA050-12UC     CONNECTED
CENTER    -           -                 DISCONNECTED
RIGHT     -           -                 DISCONNECTED
(unmapped) DB0999999  MV-CA050-12UC     UNMAPPED
```

### `tests/unit/test_enumeration.py`

Serial-to-position resolution is pure logic and must be tested without hardware.

Cover: all configured cameras found; one configured camera missing; an unmapped serial present;
duplicate serials from the SDK handled without crashing; empty discovery list.

### `tests/unit/test_import_boundaries.py`

Walk the AST of every module under `src/fcas` and assert that `MvImport` and its submodules are
imported only by `fcas.camera.mvs_sdk`. Extend it in Unit 06 to cover `pika`.

This test exists because in Python a single stray import silently defeats the layering, and it
would not be noticed until the day someone tries to run the pipeline tests on a machine with no
MVS installation.

## Dependencies

- Hikrobot MVS Python SDK — already installed, resolved at runtime via `MVCAM_COMMON_RUNENV`.
  **Not** a pip package. Never vendored into the repo, never modified.

No new pip packages.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] `fcas list-cameras` prints the table and exits 0
- [ ] The connected MV-CA050-12UC appears with serial `DB0717739` mapped to `LEFT`
- [ ] Configured-but-absent cameras show as `DISCONNECTED`
- [ ] A camera whose serial is not in config shows as `UNMAPPED` and is excluded
- [ ] SDK version and interpreter version are logged at startup
- [ ] Unsetting `MVCAM_COMMON_RUNENV` produces a clear error naming the variable, **not** an
      `ImportError` traceback from a vendor file
- [ ] Running `list-cameras` twice in a row succeeds — no handle or SDK leak between runs
- [ ] Unplugging the camera and rerunning reports it missing rather than crashing
- [ ] `test_import_boundaries` passes and genuinely fails when a stray `MvImport` import is added
      to another module (confirm by trying it, then reverting)
- [ ] No ctypes type appears in any signature outside `fcas/camera/`
- [ ] All tests pass, including Unit 01's
- [ ] Committed as `feat(unit-02): MVS Python SDK wrapper and camera enumeration`
