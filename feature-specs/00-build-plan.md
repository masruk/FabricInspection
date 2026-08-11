# Build Plan

13 units in dependency order. Each produces one verifiable result, stays within one system
boundary, and can be built in a focused session.

All 13 specs are written upfront so the whole build can be reviewed before coding starts. Treat
them as living documents: when an early unit reveals that a later spec's assumption was wrong,
update that spec before building it rather than working around it in code. Note the change under
Architecture Decisions in `progress-tracker.md`.

Every unit follows the same cycle — implement, test, verify, update tracker, commit. See
`context/ai-workflow-rules.md` → The Per-Unit Cycle.

## Unit 00 — Runtime prerequisite (not a code unit) — ✅ DONE 2026-08-11

Before Unit 01 can start, the runtime has to exist. All four steps below are complete on the
development PC; see `context/progress-tracker.md` → Environment for versions and verified facts.
They must be repeated on the vision box at Unit 09, from an offline wheelhouse.

1. Install **CPython 3.12, 64-bit** on the development PC. Confirm `python -c "import struct;
   print(struct.calcsize('P')*8)"` prints `64` — a 32-bit interpreter cannot load the 64-bit
   `MvCameraControl.dll` and the failure message is not obvious.
2. Create the project venv and confirm `pip` works.
3. Confirm the MVS Python binding imports:
   `python -c "import os,sys; sys.path.append(os.path.join(os.environ['MVCAM_COMMON_RUNENV'],'Samples','Python','MvImport')); from MvCameraControl_class import MvCamera; print(hex(MvCamera.MV_CC_GetSDKVersion()))"`
4. Record the interpreter version, venv path, and SDK version in `progress-tracker.md`.

If step 3 fails, stop and resolve it. Every later unit depends on it, and diagnosing it here is
far cheaper than diagnosing it inside a service under Session 0.

## Ordering rules applied

- Dependencies first — nothing is built on something that does not exist yet.
- Dependencies installed just in time, in the unit that first needs them.
- Camera SDK work before pipeline work before transport work.
- Mocks introduced at the point three-camera behaviour first matters, because only **one** physical
  camera exists during development.
- Service hosting last among the infrastructure units — console mode is the development loop, and
  wrapping it in the SCM early would put the service wrapper in the debug path.

## Units

### Unit 01 — Package skeleton, config, logging, console mode
**Builds:** `pyproject.toml` with `src/` layout and two console scripts, `fcas run --console`,
pydantic config schema with load and validation, structured logger with rotation, error taxonomy,
version reporting, and the `mypy`/`ruff`/`pytest` gates.
**Result:** `fcas run --console` starts, loads and validates config, logs, and exits cleanly.
Invalid config is rejected with a clear message naming every offending field.
**Dependencies:** `pydantic`, `pytest`, `mypy`, `ruff`
**Needs hardware:** no
**Spec:** `01-project-skeleton.md`

### Unit 02 — MVS SDK wrapper and camera enumeration
**Builds:** `mvs_sdk.py` (the single quarantined `MvImport` import, SDK init/finalize,
`check()` error mapping), the `ICameraDevice` Protocol, `CameraDevice` open/close, enumeration,
serial-to-position mapping from config, `UNMAPPED` detection.
**Result:** `fcas list-cameras` prints each connected camera with serial, model, and mapped
logical position; unmapped cameras are reported and excluded.
**Dependencies:** Hikrobot MVS Python SDK (already installed, loaded via `MVCAM_COMMON_RUNENV`)
**Needs hardware:** yes — one camera

### Unit 03 — Capture, debayer, buffer pool
**Builds:** `ImageBufferPool` pre-allocated at startup, `ImageLease` context manager with leak
finalizer, `scoped_frame` guard, `PixelConverter` Bayer→RGB8 into the pooled buffer, settings
application with range validation and exposure ceiling, memory budget self-check.
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
per-camera `sequence` assignment), `BoundedQueue` with explicit drop-oldest, `DropAccountant`.
**Result:** with one real camera plus two mocks, three-camera trigger events group correctly and
each image is stamped with a shared `trigger_id`. Unit tests cover missing camera, late frame,
duplicate position, and queue overflow.
**Needs hardware:** no — mocks cover it

### Unit 06 — AMQP publisher: connection, topology, publishing
**Builds:** `AmqpConnection` context manager over `pika`, idempotent exchange and queue
declaration with `x-max-length` / `drop-head` / TTL, message builder for the full header set,
publish with confirms, connection tuning.
**Result:** images appear in `frames.left` / `.center` / `.right` and are inspectable in the
RabbitMQ management UI with all headers present and correct.
**Dependencies:** `pika`
**Needs hardware:** no — local broker

### Unit 07 — Broker reconnect and degraded operation
**Builds:** disconnect detection, exponential backoff reconnect, topology redeclaration on
reconnect, `BROKER_UNAVAILABLE` accounting, `DEGRADED` state integration, `PRECONDITION_FAILED`
handling.
**Result:** stopping the broker mid-run leaves acquisition running with drops counted; restarting
it resumes publishing with no service restart and no manual queue setup.
**Needs hardware:** no

### Unit 08 — REST control server and `fcasctl`
**Builds:** Flask app served by waitress bound to the management interface, all `/api/v1`
endpoints, the standard response envelope, `fcasctl` CLI with table and `--json` output.
**Result:** `fcasctl status` prints the status table; start, stop, trigger, roll set, and config
set all work against a running service.
**Dependencies:** `flask`, `waitress`, `requests`
**Needs hardware:** no

### Unit 09 — Windows Service integration and deployment
**Builds:** `pywin32` `ServiceFramework` host, start-pending checkpoints, graceful stop within
10 s, install/uninstall commands, Event Log source, SCM failure-recovery configuration, venv
deployment procedure.
**Result:** installs as a service, auto-starts on boot with no login, stops gracefully, and
restarts automatically after a forced kill. Console mode still works unchanged.
**Dependencies:** `pywin32`
**Needs hardware:** no

### Unit 10 — Health monitor, metrics, telemetry
**Builds:** metrics aggregation, stalled-loop watchdog with escalation, telemetry JSON publishing
to `fabric.telemetry`, full `GET /status` payload.
**Result:** telemetry messages appear on the telemetry queue every 5 s; an induced acquisition
stall is detected and recovered by the watchdog.
**Needs hardware:** no

### Unit 11 — Integration guide and example consumer
**Builds:** `Documents/integration-guide.md`, `examples/consumer.py` demonstrating three-queue
consumption, grouping by `trigger_id`, `sequence` gap detection, and correct ack/prefetch.
**Result:** the ML team can consume the stream and reassemble full-width slices using only the
delivered documentation, and gaps are reported when frames are discarded.
**Dependencies:** `numpy` (example only, not a service dependency)
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
| 00 | — |
| 01 | 00 (a working interpreter) |
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

Packages are added to `pyproject.toml` and re-pinned into `requirements.lock` in the unit that
first needs them, never earlier. Every addition must have a Windows x64 wheel for CPython 3.12 —
verify before adopting, because a source-only package would need a compiler on the vision box.

| Unit | Added |
| --- | --- |
| 01 | `pydantic` (runtime); `pytest`, `pytest-timeout`, `mypy`, `ruff` (dev) |
| 02 | MVS Python SDK reference — already installed, resolved via `MVCAM_COMMON_RUNENV`, **not** a pip package and never vendored |
| 06 | `pika` |
| 08 | `flask`, `waitress`, `requests` |
| 09 | `pywin32` |
| 11 | `numpy` (example consumer only — not a service dependency) |
