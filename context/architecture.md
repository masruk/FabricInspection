# Architecture Context

Deep reference: `Documents/SDD-camera-acquisition-service.md`. This file is the working summary
the agent reads every session.

## Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Language | C++17 | Native service implementation |
| IDE / Build | **Visual Studio 2026, MSBuild, `.sln` + `.vcxproj`** | Primary development environment. No CMake. |
| Toolset | MSVC v145, x64 only, Debug + Release | Compiler and platform target |
| Dependencies | vcpkg (manifest mode, VS-integrated) | Third-party package acquisition |
| Camera SDK | Hikrobot MVS SDK (Windows C API) | USB3 Vision camera control and Bayer conversion |
| Messaging | rabbitmq-c (AMQP 0-9-1) | Publishing images to the broker |
| Broker | RabbitMQ 3.11+, local to the vision box | Per-camera queues, drop-head limiting, TTL |
| HTTP | cpp-httplib | Local REST control API |
| JSON | nlohmann/json | Config parsing, REST payloads, telemetry |
| Logging | spdlog | Rotating file logs and console output |
| Testing | GoogleTest | Unit and component tests against mocks |
| Service host | Windows SCM | Auto-start, recovery, Event Log |

## Solution Layout

Four Visual Studio projects in `FabricInspection.sln`. All source lives in a static library so
tests link the real code rather than recompiling it.

| Project | Type | Contents |
| --- | --- | --- |
| `FcasCore` | Static library (`.lib`) | All of `src/` except `main.cpp`. The entire implementation. |
| `Fcas` | Console application (`.exe`) | `main.cpp` only. Links `FcasCore`. Ships as the service binary. |
| `FcasCtl` | Console application (`.exe`) | `tools/fcasctl/`. The CLI client. |
| `FcasTests` | Console application (`.exe`) | `tests/`. GoogleTest runner. Links `FcasCore`. |

Build output goes to `build/$(Platform)/$(Configuration)/`. Intermediate files stay out of the
source tree. Only x64 is configured — Win32 is not a supported platform for this project.

## System Boundaries

- `src/service` — Windows SCM integration and application lifecycle orchestration. Owns startup
  and teardown order. Nothing else calls SCM APIs.
- `src/config` — Configuration schema, loading, validation, and profiles. The only place that
  reads the config file.
- `src/camera` — MVS SDK ownership: enumeration, device lifecycle, settings, trigger config,
  frame grabbing, debayering, hot-plug and reconnect. **The only layer that includes MVS headers.**
- `src/pipeline` — Trigger correlation, buffer pooling, bounded queues, drop accounting.
  Knows nothing about cameras or AMQP.
- `src/publish` — AMQP connection, topology declaration, message building, publishing, reconnect.
  **The only layer that includes rabbitmq-c headers.**
- `src/control` — REST server and request handlers. Delegates to other layers; contains no logic.
- `src/telemetry` — Metrics, health monitoring, watchdog, logging setup.
- `src/common` — Error types, shared value types, version. Depends on nothing.
- `tools/fcasctl` — CLI client for the REST API.
- `tests` — Unit, component, and integration tests plus mocks.

Dependencies point downward only. `src/camera` must never include an AMQP header;
`src/publish` must never include an MVS header. The currency crossing the pipeline is a pooled
`ImageBuffer` plus a plain metadata struct.

## Threading Model

| Thread | Count | Responsibility |
| --- | --- | --- |
| Service control | 1 | SCM handler, start/stop dispatch |
| CameraWorker | 1 per camera (3) | Blocking grab, debayer, hand off |
| TriggerCorrelator | 1 | Group frames, assign `trigger_id` and `sequence`, fan out |
| CameraMonitor | 1 | Hot-plug polling and reconnect backoff |
| AmqpPublisher | 1 | Drain queues, publish, confirms, reconnect |
| RestControlServer | small pool | Serve control and status requests |
| HealthMonitor | 1 | Metrics, watchdog, telemetry publish |

One thread per camera gives fault isolation and parallel debayering. A single publisher thread
exists because `rabbitmq-c` connections are not thread-safe — the connection is thread-confined
to that thread and needs no locking.

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

- Frames are grouped by **host-timestamp window** (default 200 ms), which is unambiguous because
  the trigger interval (~2800 ms) is orders of magnitude larger than inter-camera skew.
- The correlator assigns one `trigger_id` per group and a per-camera `sequence` per image.
- Images are stamped and enqueued **immediately** — the group is never buffered waiting for
  completeness. A missing camera simply produces no message for that `trigger_id`.
- Camera frame counters are tracked as a **diagnostic only** — never as an input to grouping.

## Memory Model

The buffer pool is allocated once at startup and never resized. After warm-up, FCAS performs no
large heap allocations at all. This is the mechanism by which bounded 24/7 memory is achieved.

```
frame_bytes = width x height x 3          (~15.04 MB at 2448x2048)
FCAS   = frame_bytes x pool_size
Broker = frame_bytes x x-max-length x camera_count + Erlang VM overhead
```

FCAS computes its budget at startup, logs it, and refuses to start if it exceeds the configured
ceiling.

## Invariants

1. **Acquisition never blocks on broker or network I/O.** Publishing failures discard and count;
   they never apply backpressure to capture.
2. **No large heap allocation occurs in steady state.** All image buffers come from the
   pre-allocated pool.
3. **No lock is held across a blocking SDK call, a debayer operation, or an AMQP publish.**
   Lock hold times are bounded to pointer manipulation and counter updates.
4. **Camera identity is always by serial number, never by USB port order or enumeration index.**
5. **Every MVS buffer acquired is released in the same scope, guarded by RAII.**
6. **Exceptions never cross a thread entry point or a REST handler.** Each thread body has a
   top-level catch that logs and marks the component faulted.
7. **Raw SDK and AMQP return codes are never discarded** — every wrapped error preserves the
   original value for field diagnosis.
8. **A camera or broker failure degrades the system; it never stops acquisition.** Only losing
   every camera is a FAULT.
9. **Every published message carries `trigger_id` and a per-camera monotonic `sequence`.**
   No message may be published without them.
10. **Layer isolation holds:** MVS headers only in `src/camera`, AMQP headers only in `src/publish`.
