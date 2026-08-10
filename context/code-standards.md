# Code Standards

## General

- Keep modules small and single-purpose.
- Fix root causes — do not layer workarounds.
- Do not mix unrelated concerns in one class or translation unit.
- Respect the system boundaries defined in `architecture.md`.
- Name files after the responsibility they contain, not the technology.

## Visual Studio Project Hygiene

- Every new source file is added to the `FcasCore` project, not to `Fcas`. Only `main.cpp` lives
  in `Fcas`.
- Keep `.vcxproj.filters` in sync so Solution Explorer mirrors the folder layout on disk. A file
  in `src/camera/` appears under a `camera` filter.
- Project settings that apply to all configurations go in the shared property sheet
  (`props/Common.props`), never duplicated per configuration.
- Never hardcode absolute paths in a project file. Use `$(MVCAM_COMMON_RUNENV)` for the MVS SDK
  and vcpkg for everything else.
- Do not commit `.vs/`, `build/`, `x64/`, `*.user`, or any intermediate output.
- Warning level 4 with the project's own warnings as errors. Third-party headers are included as
  external and excluded from that.

## C++

- C++17 (`/std:c++17`). Do not use language or library features beyond it.
- RAII for every resource. No naked `new`/`delete`, no manual `free`.
- Every vendor handle (MVS device handle, MVS frame buffer, AMQP connection) is owned by an RAII
  wrapper. There is no code path where a handle can leak.
- Use `std::unique_ptr` with custom deleters for C-API handles.
- Prefer value semantics and move semantics. Do not use `shared_ptr` unless ownership is genuinely
  shared; in this codebase it usually is not.
- Mark overrides `override`. Mark non-inherited classes `final` where it clarifies intent.
- `const`-correctness is required on member functions and reference parameters.
- No `using namespace` at file scope in headers.
- Headers are self-contained and use `#pragma once`.

## Error Handling

- Fallible operations return `Status`, never a bare `bool` or `int`.
- `Status` preserves the raw vendor code (`sdkRet`, `amqpRet`) alongside the mapped error code
  and a human-readable message. Never discard the vendor code.
- Error codes are namespaced by domain: `E_CFG_*`, `E_CAM_*`, `E_ACQ_*`, `E_COR_*`, `E_MQ_*`,
  `E_SVC_*`.
- Exceptions may be used internally but must never cross a thread entry point or a REST handler.
  Every thread body has a top-level `try/catch` that logs, marks the component faulted, and
  returns cleanly.
- Configuration errors are fatal. Runtime device and broker errors are recoverable.

## Concurrency

- Every shared field is either guarded by a documented mutex or is `std::atomic`. There is no
  third option.
- Document the protecting mutex in a comment next to each guarded member.
- Never hold a lock across a blocking SDK call, a debayer operation, or a network write.
- Prefer thread confinement over locking. The AMQP connection is confined to the publisher thread
  and must never be touched from another.
- Counters on the hot path are `std::atomic` with relaxed ordering unless stronger is required.
- All queues between threads are bounded. An unbounded queue is a defect.
- Threads are joined explicitly on shutdown. No detached threads.

## Memory

- Image buffers come from `ImageBufferPool` only. Never allocate an image buffer directly.
- The pool is sized and allocated at startup and never grows at runtime.
- Buffer leases are move-only RAII handles that return the buffer on destruction.
- Do not copy an image buffer. Move the lease.

## MVS SDK Usage

- MVS headers are included only within `src/camera`.
- Every `MV_CC_GetImageBuffer` is paired with `MV_CC_FreeImageBuffer` via an RAII guard in the
  same scope. Hold time is minimised — release immediately after debayering.
- Validate every parameter against the camera's reported range before applying it.
- Check every SDK return code. Log the hex value on failure.
- Do not use the SDK callback API for image delivery; use blocking pull on the worker thread.

## AMQP Usage

- rabbitmq-c headers are included only within `src/publish`.
- Topology declaration is idempotent and re-run on every reconnect.
- Messages are transient (`delivery_mode = 1`). Never publish persistent image messages.
- Message bodies point at the pooled buffer. Do not copy the image to build a message.
- Every message carries the full header set defined in `ui-context.md`. No message is published
  without `trigger_id` and `sequence`.
- Handle `PRECONDITION_FAILED` on queue declaration explicitly: log both argument sets and
  degrade. Never crash-loop.

## Configuration

- All tunable behaviour is configuration-driven. No operational constant is hardcoded.
- Validate the entire config before applying any of it. Reject invalid values with a clear message
  naming the offending field.
- Never log credentials. Reference secrets indirectly (`env:VAR_NAME`).

## Logging

- Structured, timestamped, level-tagged. Levels: `ERROR`, `WARN`, `INFO`, `DEBUG`.
- Log state transitions with old state, new state, and cause.
- Log every discarded frame with its reason and identifiers.
- Never log at `INFO` or above on a per-frame basis in steady state — it will flood the log at
  24/7 scale. Per-frame detail belongs at `DEBUG`.
- Service lifecycle events also go to the Windows Event Log.

## Testing

- Every pipeline component is unit-testable without hardware and without a broker.
- Camera access goes through `ICameraDevice` so the pipeline can run against `MockCameraDevice`.
- Only one physical camera is available during early development — multi-camera behaviour must be
  testable with mocks.
- Tests must not depend on wall-clock sleeps for correctness. Inject clocks and use deterministic
  time where behaviour is time-dependent.

## File Organization

- `src/service/` — SCM integration and lifecycle orchestration.
- `src/config/` — config schema, loading, validation.
- `src/camera/` — MVS SDK ownership, device lifecycle, workers, hot-plug.
- `src/pipeline/` — correlation, pooling, queues, drop accounting.
- `src/publish/` — AMQP connection, topology, message building, publishing.
- `src/control/` — REST server and handlers.
- `src/telemetry/` — metrics, health, watchdog, logger setup.
- `src/common/` — error types, shared types, version.
- `tools/fcasctl/` — CLI client.
- `tests/unit/`, `tests/integration/`, `tests/mocks/` — tests and test doubles.
