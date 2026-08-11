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

### `src/fcas/telemetry/metrics.py`

Central metrics registry.

- Counters: total triggers, published per camera, dropped by reason, dropped per camera, partial
  groups, frame-counter gaps per camera, broker reconnect count
- Gauges: current state, per-camera connection state, queue depths, pool free count, broker
  connection state, uptime
- Rates: achieved trigger rate over a rolling window — a rate is far more useful than a raw count
  when diagnosing a slowdown
- **One short-held `threading.Lock` for increments and for snapshot.** `counter += 1` is not atomic
  in CPython; at ~1 increment/s the lock costs nothing, and reasoning about which bytecode
  sequences happen to be safe is not a good use of anyone's afternoon.
- `snapshot()` returns an immutable dataclass; readers never see a torn view

Camera temperature is read where the camera exposes it (`DeviceTemperature` via
`MV_CC_GetFloatValue`), polled on the health thread — **never** on the acquisition path.

### `src/fcas/telemetry/health.py`

Own thread, 1 s tick on `Event.wait(1.0)`. **The clock is injected** so the watchdog is testable
without sleeping.

**Watchdog** (NFR-205): if state is `RUNNING` but no trigger has been processed within
`acquisition.watchdogTimeoutMs` (default 10× the expected trigger interval), escalate:

1. Log at `WARN` with the elapsed time since the last trigger
2. Transition to `DEGRADED`
3. Force a camera reconnect cycle
4. If still stalled after a further timeout, transition to `FAULT` and let SCM recovery restart
   the process

The watchdog must distinguish a genuine stall from a legitimately idle line. When acquisition is
started but the line is stopped, no triggers arrive and that is normal. Gate the watchdog on
having seen at least one trigger since acquisition started, and honour the
`acquisition.expectTriggers` config flag for lines that idle for long periods.

**Health checks** on each tick: broker connection state, camera connection states, queue depths
approaching capacity, **pool free count approaching zero**, disk space for logs and diagnostics.
Each threshold crossing logs once on entry and once on recovery — not on every tick.

A falling pool free count is the earliest visible symptom of a leaked lease, so surface it in
status and alert on it rather than waiting for `POOL_EXHAUSTED` drops to start.

Also sample and expose **process RSS** (via `os`/`psutil` if adopted, otherwise a Windows API call
through `ctypes`) so Unit 13's memory soak has a first-party number and does not depend on an
external sampler.

### Telemetry publishing

Publish a JSON payload to `fabric.telemetry` with routing key `status`, interval from config
(default 5 s) (FR-512).

- Small message, transient, published through the existing publisher connection. **It must be
  handed to the publisher thread, not published from the health thread** — the pika connection is
  thread-confined, and publishing telemetry from another thread is the classic way to corrupt a
  `BlockingConnection`. Enqueue a telemetry item the publisher drains alongside the image queues.
- Field names identical to `GET /status` — same serializer.
- If the broker is down, skip the publish silently. Telemetry must never queue up, never retry,
  and never contribute to drop counters — it is a status beacon, not data.
- Declare `telemetry.status` with a short TTL and a small max-length so a stopped monitor cannot
  accumulate stale status messages.

### Status integration

Extend `GET /status` and `fcasctl status` with the full metrics set. The table in `ui-context.md`
is the target format; add drop-by-reason breakdown, queue depths, and pool free count where they
fit without cluttering the default view. Put full detail behind `--json`.

### `tests/unit/test_watchdog.py`

Time-dependent logic must be deterministic — inject the clock, do not sleep.

Cover: no stall detected while triggers arrive; stall detected after the timeout; escalation
order is log → `DEGRADED` → reconnect → `FAULT`; an idle line with `expectTriggers` false does not
trigger the watchdog; recovery resets the watchdog cleanly; threshold crossings log once, not
repeatedly.

### `tests/unit/test_metrics.py`

Cover: counters increment correctly under concurrent access from many threads (this is the test
that would fail if the lock were dropped in favour of "atomic" `+=`); rate calculation over a
rolling window is correct; snapshot is internally consistent; the same serializer produces
identical output for status and telemetry.

## Dependencies

None new, unless `psutil` is adopted for RSS sampling — decide in this unit and record the
choice. A `ctypes` call to `GetProcessMemoryInfo` avoids the dependency if that is preferred.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] Telemetry messages appear on `telemetry.status` at the configured interval
- [ ] Telemetry field names match `GET /status` exactly — verified by diffing the two payloads
- [ ] Telemetry is published **from the publisher thread only** — verified by inspection and by a
      test asserting no other thread touches the connection
- [ ] With the broker down, telemetry is skipped silently and adds nothing to drop counters
- [ ] `fcasctl status` shows achieved trigger rate, drops by reason, queue depths, and pool free count
- [ ] Inducing a stall (pause the mock cameras) triggers the watchdog within the configured timeout
- [ ] Watchdog escalation follows the documented order and is visible in the log
- [ ] An idle line with `expectTriggers` false does **not** trigger the watchdog
- [ ] Recovery from a stall returns to `RUNNING` and resets the watchdog
- [ ] Camera temperature appears in status where the camera reports it
- [ ] A declining pool free count raises a threshold warning before any `POOL_EXHAUSTED` drop occurs
- [ ] Process RSS appears in status and is plausible against Task Manager
- [ ] Threshold warnings log once on crossing and once on recovery, not every tick
- [ ] Metrics remain correct after 100 000 simulated frames with no drift, exercised from
      multiple threads concurrently
- [ ] Telemetry publishing adds no measurable latency to image publishing
- [ ] All tests pass, including Units 01–09
- [ ] Committed as `feat(unit-10): health monitor, metrics, and telemetry`
