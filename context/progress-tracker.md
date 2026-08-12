# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- Implementation started. Unit 01 complete.

## Current Goal

- Unit 02 — MVS Python SDK wrapper and camera enumeration.

## Completed

- SRS v3.0 (`Documents/SRS-camera-acquisition-service.md`) — requirements, message contract,
  acceptance criteria. v3.0 changed the implementation language to Python 3.12; no functional
  requirement, message header, queue argument, state name, or acceptance criterion changed.
- SDD v3.0 (`Documents/SDD-camera-acquisition-service.md`) — threading model, buffer ownership,
  correlation algorithm, component design, plus §4.5 (GIL analysis) and §16 (C++→Python
  translation map).
- Six-file context system under `context/`, re-baselined for Python.
- Build plan (`feature-specs/00-build-plan.md`) — 13 units in dependency order.
- All 13 unit specs rewritten for Python and available for review before coding starts.
- **Unit 00 — runtime provisioning.** CPython 3.12.10 x64, venv, all dependencies, MVS
  binding verified. See Environment below.
- **Unit 01 — package skeleton, config, logging, console mode.** 93 tests; `ruff`,
  `mypy --strict`, and `pytest` all green. Details below.

## In Progress

- None.

## Next Up

- Unit 02 — MVS Python SDK wrapper and camera enumeration. **Needs the camera reconnected**
  — enumeration returned zero devices at last check.
- Unit 03 — Single-camera capture, debayer, buffer pool.

## Unit 01 — what was built

`pyproject.toml` (`src/` layout, two console scripts, ruff/mypy/pytest config),
`fcas.common` (errors, types, paths, version), `fcas.config` (pydantic schema + loader),
`fcas.telemetry.logging_setup`, `fcas.service.app`, the `fcas` and `fcasctl` entry points,
the shipped `config/fcas.config.json`, and 93 tests.

**Verified live, not just by unit test:** console run reaches `READY` and exits 0 on
Ctrl+Break; invalid config reports every problem in one run and exits 3; path resolution is
independent of the working directory; all four exit codes; the log format matches
`ui-context.md` including padding; no credential reaches stdout, stderr, or the log file.

### Two defects the live run caught that the unit tests did not

1. **`Event.wait()` with no timeout is not interruptible by a signal on Windows.** CPython
   only dispatches signal handlers in the main thread between bytecodes, and an untimed
   `wait()` ends up in an uninterruptible lock acquire — so Ctrl+C never reached the
   handler and the process had to be killed. The original unit test set the event from
   another thread, which bypasses the signal path entirely and passed. Fixed with a
   `SHUTDOWN_POLL_S` wait loop plus a regression test. **This matters for Unit 09**: the
   same wait backs `SvcStop`, and OP-104 gives shutdown a 10 s budget.
2. **`ruff format` rewrites Python code blocks inside Markdown**, which silently reformatted
   the SDD and four feature specs — files that `ai-workflow-rules.md` protects from exactly
   that. Reverted, and `extend-exclude` now keeps the formatter out of all documentation.

### Decisions taken during Unit 01

- **`FcasError` is a plain exception, not a dataclass.** A `@dataclass(frozen=True,
  slots=True)` exception renders as `(1003, 'msg')` under `str()`, and that tuple is what
  would reach the log file. SDD §9 corrected to match.
- **`print()` is permitted in the two CLI front ends only.** `fcas` must report config
  errors before logging exists; `fcasctl` output is its product. Nothing they call may
  print. `code-standards.md` refined; enforced by ruff `T20` per-file ignores.
- **`SIGBREAK` is handled alongside `SIGINT`/`SIGTERM`.** Windows delivers Ctrl+Break and a
  supervisor's `CTRL_BREAK_EVENT` as `SIGBREAK`; without it a process-group signal would
  kill the service instead of stopping it cleanly.
- **`CameraPosition.UNKNOWN` is rejected in configuration.** It is the internal sentinel for
  an unmapped camera (FR-105); accepting it from config would let an operator configure a
  camera into a state the pipeline treats as "not ours".
- **Duplicate detection runs as a second pass over the raw document.** A pydantic
  `model_validator` never runs once a field has failed, so a config with both an invalid
  position and a duplicate serial would report only one. Two passes, one combined report.
- **Config validation errors are reported all at once**, each naming its field path, because
  a restart on an inspection line is expensive and one-error-per-restart is not acceptable.

### Deviation from the spec, flagged

Unit 01 transitions to `READY` after loading configuration, as `01-project-skeleton.md`
specifies. SRS §6.1 gates `IDLE → READY` on "config loaded **and** ≥1 camera open". There is
no camera subsystem yet, so `READY` here means "configuration is valid and nothing else
exists". **Unit 04 must tighten this to match the SRS** when `CameraManager` lands. Raised
rather than silently resolved, per `ai-workflow-rules.md`.

## Environment (provisioned 2026-08-11, development PC)

| Item | Value |
| --- | --- |
| Interpreter | CPython **3.12.10, 64-bit**, all-users at `C:\Python312` (not on `PATH`; `py` launcher installed all-users) |
| Project venv | `D:\InspectionProject\Python\FabricInspection\.venv` |
| MVS SDK | `C:\Program Files (x86)\MVS\Development`, SDK version `0x4080003` (V4.8.0) |
| Broker | RabbitMQ Server **4.3.4** and Erlang OTP (`erts-17.0.5`) already installed; service `RabbitMQ` present, StartType Automatic, currently **Stopped** |

Installed into the venv (versions to pin into `requirements.lock` at Unit 01):

| Package | Version | First needed |
| --- | --- | --- |
| `pydantic` | 2.13.4 | Unit 01 |
| `pytest` | 9.1.1 | Unit 01 |
| `pytest-timeout` | 2.4.0 | Unit 01 |
| `mypy` | 2.3.0 | Unit 01 |
| `ruff` | 0.16.2 | Unit 01 |
| `pika` | 1.4.4 | Unit 06 |
| `flask` | 3.1.3 | Unit 08 |
| `waitress` | 3.0.2 | Unit 08 |
| `requests` | 2.34.2 | Unit 08 |
| `pywin32` | 312 | Unit 09 |
| `numpy` | 2.5.2 | Unit 11 (example consumer only — not a service dependency) |
| `types-requests`, `types-pywin32` | — | Unit 01 (mypy --strict) |

`pywin32_postinstall -install` has been run, so `pythonservice.exe` and the pywin32 DLLs are
registered for Unit 09. The just-in-time rule still governs `pyproject.toml`: declare each package
in the unit that first needs it, using the version above.

### Verified at provisioning time

- Interpreter is 64-bit, so it can load the 64-bit `MvCameraControl.dll`.
- The MVS Python binding imports from `%MVCAM_COMMON_RUNENV%\Samples\Python\MvImport` and
  `MV_CC_GetSDKVersion()` returns `0x4080003`.
- **ASM-009 confirmed:** `MvCameraControl_class` loads the DLL with `ctypes.WinDLL`, so the GIL is
  released for every SDK call. The threading model in SDD §4.5 stands.
- **SDD §5.1 buffer design confirmed:** a `bytearray(15_040_512)` accepts both a
  `(c_ubyte * n).from_buffer(...)` view assignable to `MV_CC_PIXEL_CONVERT_PARAM_EX.pDstBuffer`
  and a `memoryview`, and the `from_buffer` view correctly pins the `bytearray` against resizing
  (`BufferError` on append).
- `MV_FRAME_OUT_INFO_EX` exposes all of `nHostTimeStamp`, `nFrameNum`, `fExposureTime`, `fGain`,
  `nWidth`, `nHeight`, `nFrameLen`.
- **`pika` `frame_max` cap confirmed by measurement, not assumption:** `pika.spec.FRAME_MAX_SIZE`
  is 131 072; `ConnectionParameters(frame_max=1048576)` raises `ValueError`; a 15.04 MB body
  therefore becomes **115 body frames**. See Open Question 3 — the count is settled, the latency is
  not.
- **No camera was attached at provisioning time.** `MV_CC_EnumDevices(MV_USB_DEVICE)` returned
  `0x0` with `nDeviceNum = 0`. Plug in `DB0717739` and re-run the check before starting Unit 02.

## Open Questions

1. ~~No Python runtime installed.~~ **RESOLVED 2026-08-11** — CPython 3.12.10 x64 installed
   all-users at `C:\Python312`, project venv provisioned, all dependencies installed and imports
   verified. Offline wheelhouse for the *vision box* is still outstanding — see Open Question 12.
2. ~~`MVCAM_COMMON_RUNENV` scope unconfirmed.~~ **RESOLVED 2026-08-11** — it is set at **machine**
   scope on the development PC (`C:\Program Files (x86)\MVS\Development`), so a service account can
   resolve it. Re-verify on the vision box during Unit 09 deployment; the check belongs in the
   install preflight regardless.
3. **Cost of publishing a 15 MB body through `pika` is unmeasured.** The framing arithmetic is now
   confirmed — 115 body frames per image, `frame_max` cannot be raised (verified: `pika` rejects
   anything above 131 072). What is still unknown is the wall-clock cost. Measure against NFR-101
   at Unit 06; if it fails, the remedy is a different AMQP client, never a contract change.
   *Blocks: Unit 06 sign-off.*
4. **Vision box RAM unknown.** MV-VC3501X-128G60 must host FCAS + RabbitMQ + Erlang VM. Default
   profile needs ~590–630 MB combined (about 40–50 MB more than the C++ estimate, all of it
   interpreter and library overhead); constrained profile ~450–490 MB. Determines default queue
   depths and pool size. *Blocks: final config defaults.*
5. **RabbitMQ + Erlang on Windows IoT not yet verified.** RabbitMQ 4.3.4 and Erlang OTP run fine on
   the **development PC**, which is a Windows 10 Enterprise LTSC box — that is encouraging but not
   the same platform. The vision box remains unverified. If it cannot run there, the broker must
   move to another host, which changes the "publishing is local" assumption behind invariant 1.
   *Blocks: Unit 06 sign-off.*
6. **USB3 controller topology unknown.** Whether the three cameras share one root hub, and the
   port power budget. May require staggered camera start. *Blocks: Unit 04 tuning.*
7. **Trigger pitch not confirmed.** Roller circumference / cam lobe count versus the required
   ~460 mm frame pitch. *Blocks: Unit 12 position accuracy.*
8. **Inter-camera skew not measured.** The 200 ms grouping window is a calculated default, not an
   observed value. Must be validated on real hardware. *Blocks: Unit 12 verification.*
9. **`x-message-ttl` value not derived.** Must come from camera-to-marking-station distance
   divided by line speed. Currently a placeholder 5000 ms. *Blocks: Unit 06 defaults.*
10. **ML team's AMQP client library and language not confirmed.** Affects the example consumer in
    Unit 11 only; the contract itself is language-neutral.
11. **RGB8 confirmed as the training format?** ASM-005 assumes the model is trained on images
    equivalent to MVS debayer output. Needs explicit confirmation from the AI team.
12. **Offline wheelhouse for the vision box not yet built.** The development PC installed from
    PyPI over the internet. NFR-306 and OP-109 require an offline, pinned install on the vision
    box. Build it with `pip download -r requirements.lock -d wheelhouse` once Unit 01 produces the
    lockfile, and prove it with `pip install --no-index --find-links wheelhouse`.
    *Blocks: Unit 09 deployment.*

## Architecture Decisions

### Language and runtime

- **Implementation language is Python 3.12, not C++17.** Team decision, taken after the SRS/SDD
  v2.0 planning was complete. It is the team's working language and matches the consumer side.
  SRS and SDD revised to v3.0. **Only the language changed** — the message contract, topology,
  correlation algorithm, state machine, and all 15 acceptance criteria are byte-identical, so the
  Jetson consumer is unaffected.
- **Threading, not asyncio or multiprocessing.** The two expensive per-frame operations are
  `ctypes` calls through a `WinDLL` handle, which release the GIL for their duration, so per-camera
  threads overlap genuinely. asyncio was rejected because the SDK is a blocking C API with no
  awaitable surface — it would need a thread pool anyway, plus a second concurrency model.
  multiprocessing was rejected because 15 MB frames would need shared memory and a second copy to
  parallelise bytecode that is not the bottleneck. Full reasoning in SDD §4.5; to be confirmed by
  measurement at Unit 13.
- **Buffer pool is one `bytearray` per buffer with two cached views** — a `ctypes` view for
  `MV_CC_ConvertPixelTypeEx` to debayer into, and a `memoryview` for `pika` to publish. One
  allocation, two views, and no copy of the image anywhere in FCAS. Creating the `ctypes` view also
  pins the `bytearray` against resizing, which is the invariant we wanted anyway.
- **Context managers replace RAII, with a `weakref.finalize` safety net.** Every vendor resource is
  acquired through `with`. The finalizer returns a leaked lease to the pool and logs an `ERROR`; it
  is a diagnostic for bugs, never the intended path. Backed by a standard test fixture asserting
  the pool is full at teardown.
- **Exceptions internally, a result envelope at the boundaries.** v2.0 returned a `Status` from
  every fallible call because C++ could not rely on exceptions crossing an ABI. Python's
  `try`/`finally` is how deterministic cleanup is expressed here, so status-threading was dropped.
  Raw SDK and AMQP codes are still preserved on every error — that rule did not change.
- **`mypy --strict` and `ruff` are build gates, not suggestions.** They are the only mechanical
  check this language offers in place of a compiler, and the codebase is fully annotated.
- **`MvImport` is quarantined in one module** (`fcas/camera/mvs_sdk.py`) and `pika` in one package
  (`fcas/publish/`). In C++ the include boundary was enforced by the build; here it is enforced by
  a lint rule plus a test that walks the import graph.
- **Dependencies are pinned in a lockfile and installed offline from a wheelhouse.** An unpinned
  transitive upgrade must not be able to change the behaviour of a running line, and the vision box
  has no assured internet access.
- **Flask + waitress for the REST API.** Threaded WSGI, so the control plane introduces no event
  loop alongside the acquisition threads. Flask's development server is never used.

### Carried over unchanged from v2.0

- **Transport is RabbitMQ, not gRPC.** Per-camera queues with broker-side `drop-head` limiting
  replace bundled Frame Sets.
- **Broker runs on the vision box.** Publishing must be a local operation so that acquisition can
  never block on network I/O (invariant 1). A remote broker would put the network in the
  acquisition path.
- **Correlation by host-timestamp window, not frame counters.** Frame-counter correlation
  desynchronises permanently and silently after a single missed trigger. Timestamp windowing is
  unambiguous because the trigger interval is ~3 orders of magnitude larger than skew. Frame
  counters are retained as a diagnostic only.
- **Images are stamped and published immediately, never buffered for set completeness.** With
  per-camera queues there is nothing to assemble on the sender side; the consumer groups by
  `trigger_id`. This removed the partial-set timeout entirely.
- **Per-camera `sequence` header is mandatory.** Once a broker sits in the path, FCAS cannot see
  messages RabbitMQ discards after accepting them. Sequence discontinuity is the only way the
  consumer can detect uninspected fabric.
- **Messages are transient, not persistent.** Persisting ~16 MB/s of intentionally-discardable
  image data would burn disk write endurance for no benefit.
- **Buffer pool is pre-allocated at startup and never grows.** This is the mechanism for bounded
  24/7 memory, not an optimisation.
- **Single publisher thread.** A `pika` connection is not thread-safe (as `rabbitmq-c` was not);
  thread confinement removes all locking around the AMQP client.
- **`ICameraDevice` exists purely for testability.** Only one physical camera is available during
  development, so three-camera behaviour must be exercisable with mocks. It is now a
  `typing.Protocol` rather than an abstract base class, so mocks satisfy it structurally.

### Superseded by the Python port

These v2.0 decisions no longer apply and are recorded so they are not resurrected:

- Visual Studio solution with four projects (`FcasCore`, `Fcas`, `FcasCtl`, `FcasTests`),
  MSBuild, x64-only configurations. Replaced by one package plus two console scripts.
- vcpkg manifest mode for dependency acquisition. Replaced by `pyproject.toml` +
  `requirements.lock`.
- `frame_max = 1 MB` AMQP tuning. `pika` caps it at 131 072 — the one tuning parameter the port
  could not carry over. See Open Question 3.

## Session Notes

- **Hardware on hand:** one Hikrobot **MV-CA050-12UC** (serial `DB0717739`), USB3, 2448x2048
  colour. Verified working on the development PC via the MVS SDK.
- **MVS SDK** is installed at `C:\Program Files (x86)\MVS\Development`; `MVCAM_COMMON_RUNENV` is
  set at **machine** scope and the runtime DLL directories are on `PATH`.
- **MVS Python reference material** (read-only, never modified):
  - Guide: `…\MVS\Development\Documentations\Machine Vision Camera SDK Developer Guide Windows (Python) V4.8.0.chm`
  - Samples: `…\MVS\Development\Samples\Python\` — `General\GrabImage`, `General\ConvertPixelType`,
    `AreaScanCamera\MultipleCameras` are the validated call sequences to follow.
  - Binding source: `…\Samples\Python\MvImport\MvCameraControl_class.py`,
    `CameraParams_header.py`, `MvErrorDefine_const.py`, `PixelType_header.py`.
- **Verified from the binding source** during the v3.0 port:
  - The DLL is loaded with `WinDLL`, so `ctypes` releases the GIL on every SDK call (ASM-009).
  - `MV_CC_PIXEL_CONVERT_PARAM_EX.pDstBuffer` is a `POINTER(c_ubyte)`, so the SDK can debayer
    directly into a pre-allocated pooled buffer.
  - `MV_FRAME_OUT_INFO_EX` provides `nHostTimeStamp` (int64), `nFrameNum`, `fExposureTime`,
    `fGain`, and `nTriggerIndex` — enough for correlation, message headers, and diagnostics with
    no extra SDK calls on the hot path.
  - Range queries return `MVCC_FLOATVALUE(fCurValue, fMax, fMin)` and
    `MVCC_INTVALUE_EX(..., nMax, nMin, nInc)` for FR-205 validation.
- **Hardware on hand is not currently attached.** USB3 enumeration returned zero devices at
  provisioning time. Reconnect `DB0717739` before Unit 02.
- A working C++ reference app exists outside this repo at
  `C:\Users\Administrator\Documents\MvCamApp\` — enumerate, open, grab, live-view, save. Still
  useful as a known-good MVS call *sequence* for Units 02–03, even though the language differs.
- Units 01–08, 10, 11 can be built with mocks and a local broker. Units 02–04 need the one camera
  on hand. Units 12 and 13 need the trigger rig and a running line.
- The three production cameras are not yet on hand — only serial `DB0717739` is known. The other
  two entries in the config are placeholders.
