# Unit 07: Broker Reconnect and Degraded Operation

## Goal

Survive broker outages without stopping acquisition. Detect disconnection, reconnect with
exponential backoff, redeclare topology, and resume publishing — all with no service restart and
no manual broker provisioning.

## Design

This unit proves invariant 1 under fault: **acquisition never blocks on broker or network I/O.**
A broker outage is `DEGRADED`, never `FAULT` (NFR-208). Fabric keeps moving whether or not
RabbitMQ is up, so the service keeps capturing and counts what it discards.

The service must also start successfully when the broker is not yet up (NFR-202) — on boot, the
Windows service and RabbitMQ race, and FCAS must not lose that race.

## Implementation

### Disconnection detection

Signals, all converging on one code path:

- `pika.exceptions.AMQPConnectionError` / `StreamLostError` from a publish
- `pika.exceptions.ChannelClosedByBroker` / `ChannelWrongStateError` (a channel-level exception
  closes the channel; the channel cannot be reused and must be reopened)
- A missed heartbeat (the 10 s heartbeat from Unit 06), which surfaces as a stream loss
- `ConnectionBlockedTimeout` from `blocked_connection_timeout` — a broker at its memory watermark

Detection must be prompt — the consumer should not wait more than ~15 s to learn the link is gone.

On detection: mark the connection down, close the connection object cleanly (ignoring errors from
an already-dead socket), signal `ServiceApp` for the state transition, and begin the reconnect
cycle.

**Distinguish channel-level from connection-level failures.** A `PRECONDITION_FAILED` closes only
the channel and is a configuration error, not an outage; treating it as an outage produces an
endless reconnect loop against a perfectly healthy broker. Unit 06 already handles it; make sure
this unit's detection does not swallow it back into the generic path.

### Reconnect cycle

- Exponential backoff, 1 s initial, 30 s cap, retried indefinitely (FR-507)
- Reset the backoff to its initial value on a successful reconnect
- Redeclare the full topology on every reconnect — it is idempotent, and the broker may have been
  reinstalled or its definitions reset
- Log each attempt at `WARN` with the next interval, and coalesce so a long outage does not flood
  the log: log the first few attempts, then periodic summaries
- Reconnection runs on the publisher thread. **The wait is `Event.wait(interval)`**, so a stop
  request during a 30 s backoff aborts immediately and shutdown still fits the 10 s budget

Extract the backoff calculator as a small pure class with an injected clock so it is testable
without waiting.

### Startup tolerance

`AmqpPublisher.start()` must **not** raise when the broker is unreachable. It starts in the
disconnected state and enters the reconnect cycle in the background. `ServiceApp` proceeds to
`READY` and acquisition can start regardless (NFR-202).

### Drop accounting during outage

Every image that cannot be published during an outage is discarded and recorded as
`BROKER_UNAVAILABLE` with its `trigger_id` and `sequence`. Per-camera queues fill and overflow to
`LOCAL_QUEUE_FULL` — both reasons are counted separately so the log distinguishes "could not
publish" from "queue backed up".

Every discarded image's lease is closed. An outage is exactly when the pool is under most
pressure, and a leak on the failure path is a leak that only manifests during an incident.

**There is no local buffering of images to disk.** Delivery is best-effort by decision (CON-005).
The consumer detects the gap via `sequence` discontinuity when publishing resumes.

### State integration

| Condition | State |
| --- | --- |
| Cameras healthy, broker connected, acquiring | `RUNNING` |
| Cameras healthy, broker unreachable, acquiring | `DEGRADED` |
| Broker recovers | back to `RUNNING` |

Broker state and camera state combine: either being unhealthy yields `DEGRADED`. Only losing all
cameras yields `FAULT`. Log every transition with its cause.

### `tests/unit/test_reconnect.py`

Backoff logic is pure and must be tested without a broker or a sleep. Inject the clock.

Cover: intervals grow 1, 2, 4, 8, 16, 30, 30, 30; reset returns to 1; a stop request during a wait
aborts promptly; state transitions on connect and disconnect are correct; a channel-level
`PRECONDITION_FAILED` does not enter the reconnect cycle.

### `tests/integration/test_reconnect.py`

Marked `@pytest.mark.broker`; requires a controllable local broker; skip cleanly when unavailable.

Cover: service starts with broker down and reaches `READY`; publishing resumes automatically when
the broker comes up; stopping the broker mid-run transitions to `DEGRADED` and counts drops;
restarting it returns to `RUNNING` and redeclares topology; `sequence` continuity across the
outage shows exactly the expected gap; the pool is at full size after the outage ends.

## Dependencies

None new.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] Service starts and reaches `READY` with the broker **stopped**
- [ ] Starting the broker afterwards causes publishing to begin automatically, no restart
- [ ] Stopping the broker mid-run transitions to `DEGRADED` within ~15 s
- [ ] Acquisition continues throughout the outage — frame counts keep rising
- [ ] Drops during the outage are counted as `BROKER_UNAVAILABLE`, separate from `LOCAL_QUEUE_FULL`
- [ ] **The buffer pool returns to full size during and after the outage** — no lease leaks on the
      failure path
- [ ] Restarting the broker returns the service to `RUNNING` and redeclares topology
- [ ] Backoff intervals visible in the log, growing to the 30 s cap and resetting on success
- [ ] A long outage produces periodic summaries, not per-attempt log flooding
- [ ] Ctrl+C during a reconnect wait shuts down cleanly within 10 s
- [ ] A `PRECONDITION_FAILED` does not trigger the reconnect cycle
- [ ] After recovery, `sequence` shows a clean gap matching the counted drops — no duplicates, no rewind
- [ ] Deleting the queues while running and letting it reconnect recreates them automatically
- [ ] Ten stop/start broker cycles leak no memory, no leases, and no sockets
- [ ] All tests pass, including Units 01–06
- [ ] Committed as `feat(unit-07): broker reconnect and degraded operation`
