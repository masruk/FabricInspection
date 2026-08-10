# Unit 02: MVS SDK Wrapper and Camera Enumeration

## Goal

Introduce the Hikrobot MVS SDK behind an RAII wrapper, enumerate connected USB3 cameras, and map
each discovered serial number to its configured logical position. No image capture in this unit —
enumerate, open, close.

## Design

This unit establishes the boundary rule that holds for the rest of the project: **MVS headers are
included only within `src/camera`**. No other layer ever sees an `MV_CC_*` symbol.

`ICameraDevice` is introduced here rather than later because every subsequent unit depends on
being able to substitute a mock — only one physical camera exists during development.

A working reference call sequence exists at `C:\Users\Administrator\Documents\MvCamApp\MvCamApp.cpp`
(enumerate → create handle → open → close → destroy → finalize). Use it to confirm SDK usage, but
do not copy its structure — that file is a flat demo, this is a layered service.

## Implementation

### Project configuration

Add MVS SDK paths to `FcasCore`:

- Include directory: `$(MVCAM_COMMON_RUNENV)\Includes`
- Library directory: `$(MVCAM_COMMON_RUNENV)\Libraries\win64`
- Additional dependency: `MvCameraControl.lib`

Never hardcode the absolute install path. The runtime DLL directory is already on the system
`PATH`, so no DLL copy step is needed.

### `src/camera/MvsSdk`

Process-scoped RAII wrapper over `MV_CC_Initialize` / `MV_CC_Finalize`.

- Explicit `init()` returning `Status`; never initialise in a constructor
- `Finalize` runs exactly once at shutdown, including on the error path
- Exposes the SDK version string for logging at startup
- Not copyable, not movable

### `src/camera/ICameraDevice.h`

Pure interface covering the full device lifecycle the later units need. Declare the complete set
now so mocks and real devices stay in step:

```
open(deviceInfo) / close()
applySettings(settings)      // stub in this unit
configureTrigger(config)     // stub in this unit
startGrabbing() / stopGrabbing()   // stub in this unit
getFrame(out, timeoutMs) / releaseFrame(frame)  // stub in this unit
serial() / modelName() / position() / isConnected()
```

Methods not implemented in this unit return `E_CAM_NOT_IMPLEMENTED`. They are filled in by
Units 03 and 04.

### `src/camera/CameraDevice`

Concrete `ICameraDevice` over the MVS C API.

- Owns `void* handle` in a `std::unique_ptr` with a custom deleter calling `MV_CC_CloseDevice`
  then `MV_CC_DestroyHandle`. There must be no code path that leaks a handle.
- `open()` performs create-handle then open-device, mapping every SDK failure to a `Status` that
  preserves the raw hex return code.
- Reads model name and serial from `MV_CC_DEVICE_INFO`, decoding the USB3 branch
  (`SpecialInfo.stUsb3VInfo`). Handle the vendor's fixed-size char arrays safely — truncate at the
  first null, do not assume termination.

### `src/camera/CameraEnumerator`

- Wraps `MV_CC_EnumDevices` for `MV_USB_DEVICE` only. GigE and GenTL transports are out of scope
  and must not be enumerated.
- Returns a vector of plain `DiscoveredCamera` structs (serial, model, index) — no MVS types
  escape this layer.
- Resolves each discovered serial against the configured camera list, producing a mapped position
  or `UNMAPPED`.
- Logs unmapped serials as a warning and excludes them from any further use (FR-105).
- Reports configured cameras that were not discovered as missing.

### `src/service` wiring

Extend `ServiceApp::start()` to initialise `MvsSdk` after config validation and log the SDK
version. Extend teardown to finalise it last.

Add a `--list-cameras` mode to `main.cpp` that enumerates, prints the table, and exits without
entering the run loop.

Output format:

```
POSITION  SERIAL      MODEL             STATE
LEFT      DB0717739   MV-CA050-12UC     CONNECTED
CENTER    -           -                 DISCONNECTED
RIGHT     -           -                 DISCONNECTED
(unmapped) DB0999999  MV-CA050-12UC     UNMAPPED
```

### `tests/unit/enumeration_test.cpp`

Serial-to-position resolution is pure logic and must be tested without hardware. Extract the
mapping function so it takes a discovered list plus a configured list and returns the resolution.

Cover: all configured cameras found; one configured camera missing; an unmapped serial present;
duplicate serials from the SDK handled without crashing; empty discovery list.

## Dependencies

- Hikrobot MVS SDK — already installed, referenced via `$(MVCAM_COMMON_RUNENV)`

No new vcpkg packages.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] `Fcas.exe --console --list-cameras` prints the table and exits 0
- [ ] The connected MV-CA050-12UC appears with serial `DB0717739` mapped to `LEFT`
- [ ] Configured-but-absent cameras show as `DISCONNECTED`
- [ ] A camera whose serial is not in config shows as `UNMAPPED` and is excluded
- [ ] SDK version is logged at startup
- [ ] Running `--list-cameras` twice in a row succeeds — no handle or SDK leak between runs
- [ ] Unplugging the camera and rerunning reports it missing rather than crashing
- [ ] No MVS header is included outside `src/camera`
- [ ] All unit tests pass, including Unit 01's
- [ ] Committed as `feat(unit-02): MVS SDK wrapper and camera enumeration`
