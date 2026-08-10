# Unit 10: Health Monitor, Metrics, and Telemetry

## Goal

Aggregate operational metrics, detect and recover a stalled acquisition loop, and publish periodic
telemetry to the broker so the line can be monitored without polling the REST API.

## Design

A 24/7 service that fails silently is worse than one that crashes — a crash gets noticed, a stall
does not. The watchdog exists so that "the service is running" and "the service is working" are
the same statement.

The telemetry payload and the `GET /status` response come from **one serializer**. Two
serializers would drift, and the drift would be discovered during an incident.

## Implementation

### `src/telemetry/Metrics`

Central metrics registry, lock-free on the hot path.

- Counters: total triggers, published per camera, dropped by reason, dropped per camera, partial
  groups, frame-counter gaps per camera, broker reconnect count
- Gauges: current state, per-camera connection state, queue depths, pool free count, broker
  connection state, uptime
- Rates: achieved trigger rate over a rolling window — a rate is far more useful than a raw count
  when diagnosing a slowdown
- All hot-path counters are `std::atomic` with relaxed ordering
- Exposes an immutable snapshot; readers never see a torn view

Camera temperature is read where the camera exposes it (`DeviceTemperature`), polled on the health
thread — **never** on the acquisition path.

### `src/telemetry/HealthMonitor`

Own thread, 1 s tick.

**Watchdog** (NFR-205): if state is `RUNNING` but no trigger has been processed within
`watchdogTimeoutMs` (default 10× the expected trigger interval), escalate:

1. Log at `WARN` with the elapsed time since the last trigger
2. Transition to `DEGRADED`
3. Force a camera reconnect cycle
4. If still stalled after a further timeout, transition to `FAULT` and let SCM recovery restart
   the process

The watchdog must distinguish a genuine stall from a legitimately idle line. When acquisition is
started but the line is stopped, no triggers arrive and that is normal. Gate the watchdog on
having seen at least one trigger since acquisition started, and expose an explicit
`expectTriggers` config flag for lines that idle for long periods.

**Health checks** on each tick: broker connection state, camera connection states, queue depths
approaching capacity, pool free count approaching zero, disk space for logs and diagnostics.
Each threshold crossing logs once on entry and once on recovery — not on every tick.

### Telemetry publishing

Publish a JSON payload to `fabric.telemetry` with routing key `status`, interval from config
(default 5 s) (FR-512).

- Small message, transient, published through the existing `AmqpPublisher` connection
- Field names identical to `GET /status` — same serializer
- If the broker is down, skip the publish silently. Telemetry must never queue up, never retry,
  and never contribute to drop counters — it is a status beacon, not data.
- Declare `telemetry.status` with a short TTL and a small max-length so a stopped monitor cannot
  accumulate stale status messages

### Status integration

Extend `GET /status` and `fcasctl status` with the full metrics set. The table in `ui-context.md`
is the target format; add drop-by-reason breakdown and queue depths where they fit without
cluttering the default view. Put full detail behind `--json`.

### `tests/unit/watchdog_test.cpp`

Time-dependent logic must be deterministic — inject the clock, do not sleep.

Cover: no stall detected while triggers arrive; stall detected after the timeout; escalation
order is log → `DEGRADED` → reconnect → `FAULT`; an idle line with `expectTriggers` false does not
trigger the watchdog; recovery resets the watchdog cleanly; threshold crossings log once, not
repeatedly.

### `tests/unit/metrics_test.cpp`

Cover: counters increment correctly under concurrent access; rate calculation over a rolling
window is correct; snapshot is internally consistent; the same serializer produces identical
output for status and telemetry.

## Dependencies

None new.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] Telemetry messages appear on `telemetry.status` at the configured interval
- [ ] Telemetry field names match `GET /status` exactly — verified by diffing the two payloads
- [ ] With the broker down, telemetry is skipped silently and adds nothing to drop counters
- [ ] `fcasctl status` shows achieved trigger rate, drops by reason, and queue depths
- [ ] Inducing a stall (pause the mock cameras) triggers the watchdog within the configured timeout
- [ ] Watchdog escalation follows the documented order and is visible in the log
- [ ] An idle line with `expectTriggers` false does **not** trigger the watchdog
- [ ] Recovery from a stall returns to `RUNNING` and resets the watchdog
- [ ] Camera temperature appears in status where the camera reports it
- [ ] Threshold warnings log once on crossing and once on recovery, not every tick
- [ ] Metrics remain correct after 100 000 simulated frames with no counter overflow or drift
- [ ] Telemetry publishing adds no measurable latency to image publishing
- [ ] All unit tests pass, including Units 01–09
- [ ] Committed as `feat(unit-10): health monitor, metrics, and telemetry`
