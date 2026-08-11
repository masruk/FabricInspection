# Architecture Context

Deep reference: `Documents/SDD-camera-acquisition-service.md`. This file is the working summary
the agent reads every session.

## Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Language | **Python 3.12 (CPython, 64-bit)** | Service implementation |
| Packaging | **`pyproject.toml`, `src/` layout, pinned venv** | One installable package, two console scripts |
| Dependency pinning | **`requirements.lock` + offline wheelhouse** | Exact versions; the vision box has no assured internet |
| Camera SDK | **Hikrobot MVS Python SDK** (`MvImport`, ctypes over `MvCameraControl.dll`) | USB3 Vision camera control and Bayer conversion |
| Messaging | **`pika`** (AMQP 0-9-1, `BlockingConnection`) | Publishing images to the broker |
| Broker | RabbitMQ 3.11+, local to the vision box | Per-camera queues, drop-head limiting, TTL |
| HTTP | **Flask + waitress** | Local REST control API on a threaded WSGI server |
| Config | **pydantic v2** + stdlib `json` | Schema, validation with field-named errors |
| Logging | stdlib `logging` + `RotatingFileHandler` | Rotating file logs and console output |
| Testing | **pytest** | Unit and component tests against mocks |
| Static checks | **`mypy --strict`, `ruff`** | The compiler this language does not have — both are gates |
| Service host | **`pywin32`** (`win32serviceutil`, `servicemanager`) | Auto-start, recovery, Event Log |
| CLI client | `argparse` + `requests` | `fcasctl` |

Python is not installed on the development PC or the vision box yet — that is the first
deployment task, not an assumption. See `progress-tracker.md` → Open Questions.

## Package Layout

One installable package under `src/`, plus tests and examples. The `src/` layout is deliberate:
it makes it impossible to import `fcas` from the source tree instead of the installed package,
which is the usual way a Python test suite ends up validating code that is not the code that
ships.

```
src/fcas/
  common/     errors.py  types.py  version.py  paths.py
  config/     schema.py  loader.py
  camera/     mvs_sdk.py  device.py  interface.py  enumerator.py
              worker.py  manager.py  pixel_converter.py  scoped_frame.py
  pipeline/   correlator.py  buffer_pool.py  bounded_queue.py  drops.py
  publish/    publisher.py  connection.py  topology.py  message.py
  control/    rest_server.py  handlers.py  envelope.py
  telemetry/  metrics.py  health.py  logging_setup.py
  service/    app.py  windows_service.py
  fcasctl/    __main__.py  commands.py  formatting.py
  __main__.py
tests/        unit/  integration/  mocks/  conftest.py
examples/     consumer.py
```

Entry points: `fcas` (service and diagnostics) and `fcasctl` (CLI client), both declared in
`pyproject.toml`.

## System Boundaries

- `fcas/service` — SCM integration and application lifecycle orchestration. Owns startup
  and teardown order. Nothing else calls `pywin32` service APIs.
- `fcas/config` — configuration schema, loading, validation, and profiles. The only place that
  reads the config file.
- `fcas/camera` — MVS SDK ownership: enumeration, device lifecycle, settings, trigger config,
  frame grabbing, debayering, hot-plug and reconnect. **The only layer that touches `MvImport`,
  and within it only `mvs_sdk.py` imports it.**
- `fcas/pipeline` — trigger correlation, buffer pooling, bounded queues, drop accounting.
  Knows nothing about cameras or AMQP.
- `fcas/publish` — AMQP connection, topology declaration, message building, publishing, reconnect.
  **The only package that imports `pika`.**
- `fcas/control` — REST server and request handlers. Delegates to other layers; contains no logic.
- `fcas/telemetry` — metrics, health monitoring, watchdog, logging setup.
- `fcas/common` — error types, shared value types, version, path resolution. Depends on nothing.
- `fcas/fcasctl` — CLI client for the REST API.
- `tests` — unit, component, and integration tests plus mocks.

Dependencies point downward only. `fcas/camera` must never import `pika`; `fcas/publish` must
never import `MvImport`. The currency crossing the pipeline is a pooled `ImageBuffer` plus a plain
metadata dataclass.

**In Python nothing enforces this but us**, so it is enforced twice: a `ruff` banned-import rule,
and a test that walks the AST of every module and fails on a boundary violation.

## Threading Model

| Thread | Count | Responsibility |
| --- | --- | --- |
| Service control | 1 | SCM handler (`SvcDoRun`/`SvcStop`), start/stop dispatch |
| CameraWorker | 1 per camera (3) | Blocking grab, debayer, hand off |
| TriggerCorrelator | 1 | Group frames, assign `trigger_id` and `sequence`, fan out |
| CameraMonitor | 1 | Hot-plug polling and reconnect backoff |
| AmqpPublisher | 1 | Drain queues, publish, confirms, reconnect |
| RestControlServer | small pool | Serve control and status requests (waitress) |
| HealthMonitor | 1 | Metrics, watchdog, telemetry publish |

One thread per camera gives fault isolation and parallel debayering. A single publisher thread
exists because a `pika` connection is not thread-safe — it is thread-confined to that thread and
needs no locking.

**Every sleep is `Event.wait(timeout)`, never `time.sleep`.** A stop request must abort any wait
immediately, including one in the middle of a 30 s reconnect backoff, so shutdown fits its 10 s
budget.

### The GIL does not invalidate this

The two expensive operations per frame — `MV_CC_GetImageBuffer` (blocking) and
`MV_CC_ConvertPixelTypeEx` (the debayer) — are `ctypes` calls through a `WinDLL` handle, and
`ctypes` releases the GIL for the duration of every such call. The three camera threads block and
debayer genuinely in parallel. Python-level work per frame is a few hundred bytecodes against a
2 800 ms trigger interval.

`asyncio` was rejected (the SDK is a blocking C API with no awaitable surface, so it would need a
thread pool anyway, plus a second concurrency model). `multiprocessing` was rejected (15 MB frames
would need shared memory and a second copy to parallelise bytecode that is not the bottleneck).
Full reasoning: SDD §4.5.

## Storage Model

This system has **no database**. Nothing is persisted except configuration, logs, and optional
diagnostics.

- **Config file (JSON, on disk)**: camera serial-to-position mapping, camera settings, broker
  connection, queue arguments, acquisition parameters. Read at startup; hot-reloadable settings
  applied at runtime.
- **RabbitMQ queues (memory, transient)**: images in flight. Bounded by `x-max-length` and
  `x-message-ttl`. Never persisted to disk.
- **Rotating log files (disk)**: structured operational logs, bounded by retention policy.
- **Windows Event Log**: service lifecycle events only.
- **Diagnostic image dumps (disk, off by default)**: bounded by count and size.

Image data is never written to disk in normal operation and never stored in a database.

## Access and Security Model

- FCAS runs under a dedicated Windows service account with least privilege.
- The REST control API binds to the management interface only, never the plant network by default.
- Broker credentials come from configuration referencing an environment variable; they are never
  hardcoded and never written to logs.
- The broker uses a dedicated non-default user scoped to the required exchange and queues.
- `MVCAM_COMMON_RUNENV` must be **machine-scope**, or the service account cannot resolve the MVS
  binding and startup fails with a confusing `ImportError`.

## Delivery Model

Delivery is **best-effort**. Frames are discarded rather than allowed to accumulate. Loss occurs
at two distinct points and only one is visible to FCAS:

| Where | Cause | Detected by | Mechanism |
| --- | --- | --- | --- |
| Local (inside FCAS) | Broker down, queue full, pool exhausted, camera missing | FCAS | `DropAccountant` counters, logs, telemetry |
| Broker (`drop-head` / TTL) | Consumer too slow or stopped | Consumer | `sequence` header discontinuity |

The per-camera monotonic `sequence` header is what preserves the coverage audit trail across the
broker boundary. It is not optional.

## Trigger Correlation Model

Hardware fires all three cameras at once but provides no shared trigger identifier, and each
camera reports only a camera-local frame counter.

- Frames are grouped by **host-timestamp window** (default 200 ms) using
  `MV_FRAME_OUT_INFO_EX.nHostTimeStamp` — the host stamp, not the device stamp, because device
  clocks are not synchronised to each other.
- The window is unambiguous because the trigger interval (~2800 ms) is orders of magnitude larger
  than inter-camera skew.
- The correlator assigns one `trigger_id` per group and a per-camera `sequence` per image.
- Images are stamped and enqueued **immediately** — the group is never buffered waiting for
  completeness. A missing camera simply produces no message for that `trigger_id`.
- Camera frame counters are tracked as a **diagnostic only** — never as an input to grouping.

## Memory Model

The buffer pool is allocated once at startup and never resized. After warm-up, FCAS performs no
large allocations at all. This is the mechanism by which bounded 24/7 memory is achieved.

Each pooled buffer is **one `bytearray` with two cached views**:

- `(ctypes.c_ubyte * n).from_buffer(ba)` → the destination `MV_CC_ConvertPixelTypeEx` debayers
  into. Creating it also pins the `bytearray` so it can never be resized — the invariant we want,
  enforced by the interpreter.
- `memoryview(ba)` → the body handed to `pika.basic_publish`.

One allocation, two views, **no copy of the image anywhere in FCAS**.

```
frame_bytes = width x height x 3          (~15.04 MB at 2448x2048)
FCAS   = frame_bytes x pool_size + ~90-120 MB runtime overhead
Broker = frame_bytes x x-max-length x camera_count + Erlang VM overhead
```

FCAS computes its budget at startup, logs it, and refuses to start if it exceeds the configured
ceiling. `gc.freeze()` runs once after startup so the collector never re-walks the startup object
graph; the cyclic collector stays enabled because the hot path creates no cycles.

## Resource Ownership Model

Python has no destructors you can rely on, so every vendor resource is acquired through a context
manager and released in a `finally`:

| Resource | Acquired by | Released by |
| --- | --- | --- |
| MVS frame buffer | `with scoped_frame(device, frame):` | `MV_CC_FreeImageBuffer` in `finally` |
| MVS device handle | `CameraDevice` as a context manager | `MV_CC_CloseDevice` + `MV_CC_DestroyHandle` |
| Pooled image buffer | `pool.acquire()` → `ImageLease` | `lease.close()`, always in a `finally` |
| pika connection | `AmqpConnection` context manager | closed on exit and on reconnect |

`ImageLease` also carries a `weakref.finalize` that returns the buffer **and logs
`ERROR pipeline lease leaked`**. That is a safety net, not the mechanism — a leak that reaches it
is a bug to fix, and the log line is how it gets found in the field rather than a week later as a
mysterious `POOL_EXHAUSTED`.

## Invariants

1. **Acquisition never blocks on broker or network I/O.** Publishing failures discard and count;
   they never apply backpressure to capture.
2. **No large allocation occurs in steady state.** All image buffers come from the pre-allocated
   pool, and no image is ever copied.
3. **No lock is held across a blocking SDK call, a debayer operation, or an AMQP publish.**
   Lock hold times are bounded to container manipulation and counter updates.
4. **Camera identity is always by serial number, never by USB port order or enumeration index.**
5. **Every MVS buffer acquired is released in the same `with` block; every pooled lease is closed
   in a `finally`.** There is no bare acquire anywhere in the codebase.
6. **Exceptions never escape a thread entry point or a REST handler.** Each thread body has a
   top-level `except Exception` that logs and marks the component faulted; Flask has a catch-all
   error handler; `threading.excepthook` is the final backstop.
7. **Raw SDK and AMQP return codes are never discarded** — every wrapped error preserves the
   original value for field diagnosis, and `raise ... from exc` preserves the traceback.
8. **A camera or broker failure degrades the system; it never stops acquisition.** Only losing
   every camera is a FAULT.
9. **Every published message carries `trigger_id` and a per-camera monotonic `sequence`.**
   No message may be published without them.
10. **Layer isolation holds:** `MvImport` only in `fcas/camera/mvs_sdk.py`, `pika` only in
    `fcas/publish/`. Enforced by lint and by an import-graph test.
11. **Everything is type-annotated and passes `mypy --strict` and `ruff`.** These are gates, not
    suggestions — they are the only mechanical check this language offers.
