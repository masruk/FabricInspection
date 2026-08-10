# Build Plan

13 units in dependency order. Each produces one verifiable result, stays within one system
boundary, and can be built in a focused session.

All 13 specs are written upfront so the whole build can be reviewed before coding starts. Treat
them as living documents: when an early unit reveals that a later spec's assumption was wrong,
update that spec before building it rather than working around it in code. Note the change under
Architecture Decisions in `progress-tracker.md`.

Every unit follows the same cycle — implement, test, verify, update tracker, commit. See
`context/ai-workflow-rules.md` → The Per-Unit Cycle.

## Ordering rules applied

- Dependencies first — nothing is built on something that does not exist yet.
- Dependencies installed just in time, in the unit that first needs them.
- Camera SDK work before pipeline work before transport work.
- Mocks introduced at the point three-camera behaviour first matters, because only **one** physical
  camera exists during development.
- Service hosting last among the infrastructure units — console mode is the development loop, and
  wrapping it in the SCM early would put the service wrapper in the debug path.

## Units

### Unit 01 — Solution skeleton, config, logging, console mode
**Builds:** Visual Studio solution with four projects (`FcasCore`, `Fcas`, `FcasCtl`, `FcasTests`),
`main.cpp` with `--console` dispatch, config schema with load and validation, structured logger
with rotation, `Status`/error taxonomy, version reporting.
**Result:** `Fcas.exe --console` starts, loads and validates config, logs, and exits cleanly.
Invalid config is rejected with a clear message naming every offending field.
**Dependencies:** nlohmann-json, spdlog, gtest (vcpkg manifest)
**Needs hardware:** no
**Spec:** `01-project-skeleton.md`

### Unit 02 — MVS SDK wrapper and camera enumeration
**Builds:** `MvsSdk` RAII init/finalize, `ICameraDevice` interface, `CameraDevice` open/close,
enumeration, serial-to-position mapping from config, `UNMAPPED` detection.
**Result:** `fcas.exe --console --list-cameras` prints each connected camera with serial, model,
and mapped logical position; unmapped cameras are reported and excluded.
**Dependencies:** Hikrobot MVS SDK (already installed)
**Needs hardware:** yes — one camera

### Unit 03 — Capture, debayer, buffer pool
**Builds:** `ImageBufferPool` pre-allocated at startup, `ImageLease` RAII, `ScopedMvsFrame` guard,
`PixelConverter` Bayer→RGB8, settings application with range validation and exposure ceiling,
memory budget self-check.
**Result:** captures N frames from one camera and writes RGB8 images to disk. Budget is logged at
startup. Pool exhaustion is handled and counted, never crashes.
**Needs hardware:** yes — one camera

### Unit 04 — CameraWorker, CameraManager, hot-plug and recovery
**Builds:** per-camera acquisition threads, `CameraManager` registry, enumeration polling,
reconnect with exponential backoff, aggregate state driving `RUNNING`/`DEGRADED`/`FAULT`.
**Result:** unplugging a camera moves the service to `DEGRADED` and logs it; replugging recovers
to `RUNNING` within 30 s without restart. Logical position survives a port change.
**Needs hardware:** yes — one camera, physically unplugged during test

### Unit 05 — MockCameraDevice, correlation, queues, drop accounting
**Builds:** `MockCameraDevice`, `TriggerCorrelator` (timestamp windowing, `trigger_id` and
per-camera `sequence` assignment), `BoundedQueue` with drop-oldest, `DropAccountant`.
**Result:** with one real camera plus two mocks, three-camera trigger events group correctly and
each image is stamped with a shared `trigger_id`. Unit tests cover missing camera, late frame,
duplicate position, and queue overflow.
**Needs hardware:** no — mocks cover it

### Unit 06 — AMQP publisher: connection, topology, publishing
**Builds:** `AmqpConnection` RAII, idempotent exchange and queue declaration with `x-max-length` /
`drop-head` / TTL, `MessageBuilder` for the full header set, publish with confirms, connection
tuning (`frame_max`, heartbeat).
**Result:** images appear in `frames.left` / `.center` / `.right` and are inspectable in the
RabbitMQ management UI with all headers present and correct.
**Dependencies:** rabbitmq-c
**Needs hardware:** no — local broker

### Unit 07 — Broker reconnect and degraded operation
**Builds:** disconnect detection, exponential backoff reconnect, topology redeclaration on
reconnect, `BROKER_UNAVAILABLE` accounting, `DEGRADED` state integration,
`PRECONDITION_FAILED` handling.
**Result:** stopping the broker mid-run leaves acquisition running with drops counted; restarting
it resumes publishing with no service restart and no manual queue setup.
**Needs hardware:** no

### Unit 08 — REST control server and `fcasctl`
**Builds:** HTTP server bound to the management interface, all `/api/v1` endpoints, the standard
response envelope, `fcasctl` CLI with table and `--json` output.
**Result:** `fcasctl status` prints the status table; start, stop, trigger, roll set, and config
set all work against a running service.
**Dependencies:** cpp-httplib
**Needs hardware:** no

### Unit 09 — Windows Service integration
**Builds:** `WindowsService` SCM integration, `ServiceMain`, start-pending checkpoints, graceful
stop within 10 s, install/uninstall commands, Event Log source, SCM failure-recovery configuration.
**Result:** installs as a service, auto-starts on boot with no login, stops gracefully, and
restarts automatically after a forced kill. Console mode still works unchanged.
**Needs hardware:** no

### Unit 10 — Health monitor, metrics, telemetry
**Builds:** metrics aggregation, stalled-loop watchdog with escalation, telemetry JSON publishing
to `fabric.telemetry`, full `GET /status` payload.
**Result:** telemetry messages appear on the telemetry queue every 5 s; an induced acquisition
stall is detected and recovered by the watchdog.
**Needs hardware:** no

### Unit 11 — Integration guide and Python example consumer
**Builds:** `Documents/integration-guide.md`, `examples/consumer.py` demonstrating three-queue
consumption, grouping by `trigger_id`, `sequence` gap detection, and correct ack/prefetch.
**Result:** the ML team can consume the stream and reassemble full-width slices using only the
delivered documentation, and gaps are reported when frames are discarded.
**Dependencies:** pika (Python side only)
**Needs hardware:** no

### Unit 12 — Hardware trigger and position accumulation
**Builds:** hardware trigger configuration on all cameras, trigger pitch to `position_mm`
accumulation, roll ID reset, inter-camera skew measurement, grouping window validation.
**Result:** a physical trigger pulse produces one message per camera with a shared `trigger_id`
and correct `position_mm`. Measured skew confirms or corrects the grouping window.
**Needs hardware:** yes — three cameras, trigger rig, running line

### Unit 13 — Soak and performance validation
**Builds:** instrumentation at each stage boundary, soak harness with RSS sampling for FCAS and
broker, load generator for the 2 triggers/s stress case.
**Result:** 24 h run with zero unexplained drops; p99 trigger-to-broker latency at or below
300 ms; memory growth under 5% over 7 days.
**Needs hardware:** yes — full rig

## Dependency check

| Unit | Requires |
| --- | --- |
| 01 | — |
| 02 | 01 (config, logging) |
| 03 | 02 (device access) |
| 04 | 03 (capture path) |
| 05 | 04 (workers) |
| 06 | 05 (stamped images to publish) |
| 07 | 06 (connection to lose) |
| 08 | 04, 07 (state to report and control) |
| 09 | 08 (a complete app to host) |
| 10 | 08 (status payload shape) |
| 11 | 06 (real messages to consume) |
| 12 | 05, 06 (correlation and publishing) |
| 13 | 09, 10 (deployed service with metrics) |

Every unit depends only on earlier units.

## Spec index

| Unit | Spec file |
| --- | --- |
| 01 | `01-project-skeleton.md` |
| 02 | `02-camera-enumeration.md` |
| 03 | `03-capture-debayer-pool.md` |
| 04 | `04-camera-workers-recovery.md` |
| 05 | `05-correlation-queues-drops.md` |
| 06 | `06-amqp-publisher.md` |
| 07 | `07-broker-reconnect.md` |
| 08 | `08-rest-control-cli.md` |
| 09 | `09-windows-service.md` |
| 10 | `10-health-metrics-telemetry.md` |
| 11 | `11-integration-guide-consumer.md` |
| 12 | `12-hardware-trigger-position.md` |
| 13 | `13-soak-performance.md` |

## Hardware availability

Units 01, 05, 06, 07, 08, 09, 10, 11 need no camera. Units 02, 03, 04 need the one camera already
on hand. Only Units 12 and 13 are blocked on the full rig — so roughly 85% of the build can
proceed before the production cameras and trigger hardware arrive.

## Dependency introduction schedule

Packages are added to `vcpkg.json` in the unit that first needs them, never earlier.

| Unit | Added |
| --- | --- |
| 01 | `nlohmann-json`, `spdlog`, `gtest` |
| 02 | MVS SDK reference (already installed, via `$(MVCAM_COMMON_RUNENV)`) |
| 06 | `librabbitmq` |
| 08 | `cpp-httplib` |
| 11 | `pika`, `numpy` (Python side only — not in the C++ build) |
