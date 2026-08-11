# Code Standards

## General

- Keep modules small and single-purpose.
- Fix root causes — do not layer workarounds.
- Do not mix unrelated concerns in one class or module.
- Respect the system boundaries defined in `architecture.md`.
- Name files after the responsibility they contain, not the technology.

## Python Language Level

- Python 3.12. Do not use syntax or stdlib features newer than 3.12.
- Modern syntax throughout: `X | None` not `Optional[X]`, `list[str]` not `List[str]`,
  `match` where it genuinely reads better than `if`/`elif`.
- `from __future__ import annotations` at the top of every module.
- Dataclasses for value types. `@dataclass(frozen=True, slots=True)` unless mutability is
  required and justified.
- `Enum` / `StrEnum` for closed sets — positions, states, drop reasons, error codes. Never a bare
  string constant where an enum belongs.
- `typing.Protocol` for interfaces, not ABCs. `ICameraDevice` is a Protocol so mocks satisfy it
  structurally.
- No mutable default arguments. No `*` imports outside the one quarantined SDK shim (below).

## Typing

- **Every function, method, and module-level name is annotated.** No implicit `Any`.
- `mypy --strict` must pass with zero errors. It is a gate, not a suggestion — it is the only
  mechanical check this project gets in place of a compiler.
- `ruff` must pass with zero findings. Format with `ruff format`.
- `# type: ignore` requires a specific error code and a comment explaining why. The `ctypes`
  boundary is the only place these are expected.
- Never annotate something as `Any` to silence a checker. Type the boundary properly once, in the
  wrapper, and keep `Any` out of every other module.

## Project Structure

- `src/` layout. The package is installed (`pip install -e .`) and imported as `fcas`, never by
  path manipulation.
- Every new module goes in the package directory matching its boundary in `architecture.md`.
- Dependencies are declared in `pyproject.toml` and pinned in `requirements.lock`. Add a
  dependency only in the unit that first needs it.
- Do not commit `.venv/`, `__pycache__/`, `*.pyc`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`,
  `build/`, `dist/`, `*.egg-info/`, log files, or diagnostic images.

## Resource Management

Python has no destructors you can rely on. This section is the substitute, and it is not optional.

- **Every vendor resource is acquired through a context manager.** MVS frames, MVS device handles,
  pooled buffers, pika connections. There is no bare acquire anywhere in the codebase.
- Every `MV_CC_GetImageBuffer` is inside `with scoped_frame(...)`, which calls
  `MV_CC_FreeImageBuffer` in a `finally`. Hold time is minimised — release immediately after
  debayering.
- Every `pool.acquire()` is released with `lease.close()` in a `finally`, or by `with lease:`.
- Do not rely on refcounting to release anything. It is an implementation detail, and a lease held
  alive by an exception traceback will outlive the scope you expected it to die in.
- `ImageLease` carries a `weakref.finalize` that returns the buffer and logs an `ERROR`. That is a
  diagnostic net for bugs, never the intended path.
- Threads are joined explicitly on shutdown, with a timeout. No daemon threads standing in for
  shutdown logic.

## Error Handling

- Errors are **exceptions internally, an envelope at the boundary**. Do not thread status objects
  through every call — that is a C++ idiom and it reads badly here.
- All exceptions derive from `FcasError`, which carries `code`, `message`, and the raw vendor
  codes `sdk_ret` / `amqp_ret`. **Never discard a vendor code.** Log SDK returns as hex.
- Error codes are namespaced by domain: `E_CFG_*`, `E_CAM_*`, `E_ACQ_*`, `E_COR_*`, `E_MQ_*`,
  `E_SVC_*`.
- Always `raise NewError(...) from exc`. A bare `raise ... from None` throws away the diagnosis.
- Never catch bare `except:`. Catch the narrowest exception that can actually occur.
- `except Exception` is permitted in exactly three places: a thread entry point, the Flask
  error handler, and `threading.excepthook`. Each logs, marks the component faulted, and returns
  cleanly.
- Configuration errors are fatal. Runtime device and broker errors are recoverable.

## Concurrency

- Every shared field is either guarded by a documented lock or is confined to one thread. There is
  no third option — and note that `+=` on an integer attribute is **not** atomic in CPython, so
  "it's just a counter" is not an exemption.
- Document the protecting lock in a comment next to each guarded attribute.
- Never hold a lock across a blocking SDK call, a debayer operation, or a network write.
- Prefer thread confinement over locking. The pika connection is confined to the publisher thread
  and must never be touched from another.
- All queues between threads are bounded. An unbounded `queue.Queue()` is a defect.
- **Every wait is `Event.wait(timeout)`.** `time.sleep` does not appear in service code — it
  cannot be interrupted by a stop request.
- Every thread is created with an explicit `name=` so it is identifiable in a stack dump.

## Memory

- Image buffers come from `ImageBufferPool` only. Never allocate an image buffer directly.
- The pool is sized and allocated at startup and never grows at runtime.
- **Never copy an image.** The SDK debayers into the pooled buffer through its `ctypes` view and
  `pika` publishes the `memoryview` of the same bytes. `bytes(buf)`, slicing that materialises, or
  `numpy.array(..., copy=True)` on the hot path are all defects.
- `gc.freeze()` is called once after startup. Do not disable the cyclic collector.
- Do not create reference cycles on the hot path.

## MVS SDK Usage

- **`MvImport` is imported in exactly one module: `fcas/camera/mvs_sdk.py`.** That module performs
  the vendor's `from MvCameraControl_class import *` once and re-exports only the named symbols
  the codebase uses. Nowhere else may import from `MvImport`, and no other module may use a
  star-import at all.
- The binding is located through `MVCAM_COMMON_RUNENV`. If it is unset, fail at startup with a
  message naming the variable — do not let it surface as an `ImportError` from a vendor file.
- Every SDK call goes through `check(ret, op)`, which raises `MvsError` carrying the hex return.
  No call site inspects a raw return code itself.
- Validate every parameter against the camera's reported range (`MVCC_FLOATVALUE.fMin/fMax`,
  `MVCC_INTVALUE_EX.nMin/nMax/nInc`) before applying it.
- Decode vendor `char` arrays with the shared helper: truncate at the first NUL, try `gbk`,
  `utf-8`, `latin-1`. These fields are not guaranteed UTF-8 or NUL-terminated.
- Do not use the SDK callback API for image delivery; use blocking pull on the worker thread. The
  exception callback may be registered, but its handler only sets an `Event` — it never calls the
  SDK, allocates, or logs, because it runs on an SDK-owned thread.
- Use frame-accurate metadata where the SDK provides it: `fExposureTime` and `fGain` from
  `MV_FRAME_OUT_INFO_EX` describe that frame, not the last value written to the camera.

## AMQP Usage

- `pika` is imported only within `fcas/publish/`.
- The connection and channel are confined to the publisher thread. Never share them.
- Topology declaration is idempotent and re-run on every reconnect.
- Messages are transient (`delivery_mode=1`). Never publish persistent image messages.
- Message bodies are a `memoryview` over the pooled buffer. Do not copy the image to build a
  message.
- Every message carries the full header set defined in `ui-context.md`. No message is published
  without `trigger_id` and `sequence`.
- Handle `ChannelClosedByBroker(406, ...)` (`PRECONDITION_FAILED`) on queue declaration
  explicitly: log both argument sets and degrade. Never crash-loop.
- The drain loop must return to pika regularly so heartbeats are serviced. A long Python-side wait
  inside `BlockingConnection` produces a broker-side heartbeat timeout that looks exactly like a
  network fault.

## Configuration

- All tunable behaviour is configuration-driven. No operational constant is hardcoded.
- The schema is pydantic v2 models. Validate the entire config before applying any of it and
  report **all** errors at once, each naming its field path (`cameras[1].exposureUs`).
- JSON field names stay `lowerCamelCase` as specified in `ui-context.md`, mapped to
  `snake_case` Python attributes with pydantic aliases. The file format is the contract; the
  Python naming is ours.
- Never log credentials. Reference secrets indirectly (`env:VAR_NAME`).

## Paths

- Every relative path resolves against the package installation directory, never `os.getcwd()`.
  A service starts in `%SystemRoot%\System32`; this is the single most common cause of "works in
  console, fails as a service".
- Use `pathlib.Path` throughout. No string path concatenation.

## Logging

- stdlib `logging` with one logger per module (`logging.getLogger(__name__)`).
- The line format is fixed by `ui-context.md`: timestamp with milliseconds, level padded to five
  characters, component tag padded to seven. A custom `Formatter` owns it.
- Log state transitions with old state, new state, and cause.
- Log every discarded frame with its reason and identifiers.
- Never log at `INFO` or above on a per-frame basis in steady state — it will flood the log at
  24/7 scale. Per-frame detail belongs at `DEBUG`.
- Use lazy formatting (`log.info("x=%s", x)`), never f-strings in log calls — the argument must
  not be formatted when the level is disabled.
- `print()` appears nowhere in service code. Under Session 0 there is no stdout.
- Service lifecycle events also go to the Windows Event Log via `servicemanager`.

## Testing

- pytest. Every pipeline component is unit-testable without hardware and without a broker.
- Camera access goes through the `ICameraDevice` Protocol so the pipeline can run against
  `MockCameraDevice`.
- Only one physical camera is available during early development — multi-camera behaviour must be
  testable with mocks.
- **Clocks are injected.** Tests must not depend on wall-clock sleeps for correctness.
- **Assert `pool.free_count == pool.size` at teardown of every pipeline test.** In a language
  without destructors this is the cheapest defence against the leak that would otherwise surface
  as a failed seven-day soak.
- Broker-dependent tests are marked `@pytest.mark.broker` and skip cleanly with a clear message
  when no broker is reachable.
- `mypy --strict` and `ruff` run alongside the test suite. A unit is not done until all three pass.

## File Organization

- `src/fcas/service/` — SCM integration and lifecycle orchestration.
- `src/fcas/config/` — config schema, loading, validation.
- `src/fcas/camera/` — MVS SDK ownership, device lifecycle, workers, hot-plug.
- `src/fcas/pipeline/` — correlation, pooling, queues, drop accounting.
- `src/fcas/publish/` — AMQP connection, topology, message building, publishing.
- `src/fcas/control/` — REST server and handlers.
- `src/fcas/telemetry/` — metrics, health, watchdog, logging setup.
- `src/fcas/common/` — error types, shared types, version, path resolution.
- `src/fcas/fcasctl/` — CLI client.
- `tests/unit/`, `tests/integration/`, `tests/mocks/` — tests and test doubles.
