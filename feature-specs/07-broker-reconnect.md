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

Three distinct signals, all converging on one code path:

- A publish returning a socket-level error
- A missed heartbeat (the 10 s heartbeat from Unit 06)
- A channel-level exception closing the channel

Detection must be prompt — the consumer should not wait more than ~15 s to learn the link is gone.

On detection: mark the connection down, tear down the connection object cleanly, signal
`ServiceApp` for the state transition, and begin the reconnect cycle.

### Reconnect cycle

- Exponential backoff, 1 s initial, 30 s cap, retried indefinitely (FR-507)
- Reset the backoff to its initial value on a successful reconnect
- Redeclare the full topology on every reconnect — it is idempotent, and the broker may have been
  reinstalled or its definitions reset
- Log each attempt at `WARN` with the next interval, and coalesce so a long outage does not flood
  the log: log the first few attempts, then periodic summaries
- Reconnection runs on the publisher thread. It must never block shutdown — a stop request during
  backoff must abort the wait and exit within the 10 s budget.

### Startup tolerance

`AmqpPublisher::start()` must **not** fail when the broker is unreachable. It starts in the
disconnected state and enters the reconnect cycle in the background. `ServiceApp` proceeds to
`READY` and acquisition can start regardless (NFR-202).

### Drop accounting during outage

Every image that cannot be published during an outage is discarded and recorded as
`BROKER_UNAVAILABLE` with its `trigger_id` and `sequence`. Per-camera queues fill and overflow to
`LOCAL_QUEUE_FULL` — both reasons are counted separately so the log distinguishes "could not
publish" from "queue backed up".

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

### `tests/unit/reconnect_test.cpp`

Backoff logic is pure and must be tested without a broker. Extract the backoff calculator so it is
independently testable.

Cover: intervals grow 1, 2, 4, 8, 16, 30, 30, 30; reset returns to 1; a stop request during a wait
aborts promptly; state transitions on connect and disconnect are correct.

### `tests/integration/reconnect_test.cpp`

Requires a controllable local broker; skip cleanly when unavailable.

Cover: service starts with broker down and reaches `READY`; publishing resumes automatically when
the broker comes up; stopping the broker mid-run transitions to `DEGRADED` and counts drops;
restarting it returns to `RUNNING` and redeclares topology; `sequence` continuity across the
outage shows exactly the expected gap.

## Dependencies

None new.

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] Service starts and reaches `READY` with the broker **stopped**
- [ ] Starting the broker afterwards causes publishing to begin automatically, no restart
- [ ] Stopping the broker mid-run transitions to `DEGRADED` within ~15 s
- [ ] Acquisition continues throughout the outage — frame counts keep rising
- [ ] Drops during the outage are counted as `BROKER_UNAVAILABLE`, separate from `LOCAL_QUEUE_FULL`
- [ ] Restarting the broker returns the service to `RUNNING` and redeclares topology
- [ ] Backoff intervals visible in the log, growing to the 30 s cap and resetting on success
- [ ] A long outage produces periodic summaries, not per-attempt log flooding
- [ ] Ctrl+C during a reconnect wait shuts down cleanly within 10 s
- [ ] After recovery, `sequence` shows a clean gap matching the counted drops — no duplicates, no rewind
- [ ] Deleting the queues while running and letting it reconnect recreates them automatically
- [ ] Ten stop/start broker cycles leak no memory and no connection handles
- [ ] All unit tests pass, including Units 01–06
- [ ] Committed as `feat(unit-07): broker reconnect and degraded operation`
