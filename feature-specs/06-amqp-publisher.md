# Unit 06: AMQP Publisher — Connection, Topology, Publishing

## Goal

Connect to RabbitMQ with `pika`, declare the exchange and per-camera queues idempotently, and
publish each stamped image to its own queue with the complete header set. Images become visible
and correct in the RabbitMQ management UI.

## Design

A `pika` connection and channel are **not thread-safe**. A single publisher thread owns them and
they are never touched from another thread. That thread confinement is why no locking is needed
around the AMQP client (`architecture.md`, threading model).

`pika` is imported only within `fcas/publish` — no other package sees a `pika` symbol. Extend the
import-boundary test from Unit 02 to cover it in this unit.

The queue arguments are what implement the AI team's "flush when over ML capacity" requirement.
They are declared by FCAS, not configured by hand on the broker, so a fresh broker needs no manual
provisioning (OP-107).

## Implementation

### Broker prerequisite

RabbitMQ must be installed and running locally. Document the exact steps in
`Documents/broker-setup.md`: install Erlang, install RabbitMQ, enable the management plugin,
create the `fcas` user with permissions scoped to the exchange and queues, delete or disable the
default `guest` account, and set `vm_memory_high_watermark` (OP-108).

> **Open question 5 blocks this unit.** RabbitMQ + Erlang on Windows IoT is unverified. Confirm
> installability on the vision box before relying on the local-broker design. If it cannot run
> there, escalate — it invalidates invariant 1's justification and the broker must move hosts.

### `src/fcas/publish/connection.py`

Context manager over a `pika.BlockingConnection`.

- Connect with `ConnectionParameters(host, port, virtual_host, credentials, ...)`, open a channel,
  enable publisher confirms with `channel.confirm_delivery()`, close everything on exit
- Connection tuning:

| Setting | Value | Reason |
| --- | --- | --- |
| `heartbeat` | 10 s | Detects a dead broker promptly (FR-507) |
| `blocked_connection_timeout` | 5 s | A broker under memory pressure sends `connection.blocked`; without this the publish thread waits indefinitely and violates FR-508 |
| `socket_timeout`, `stack_timeout` | 5 s | Bound connect attempts so Unit 07's backoff stays on schedule |
| `frame_max` | pika's maximum | See the note below |

> **`frame_max`.** The C++ design specified 1 MB to avoid chopping each 15 MB image into ~118
> frames. `pika` caps `frame_max` at `pika.spec.FRAME_MAX_SIZE` (131 072), so that tuning cannot be
> carried over — see SDD §5.2 and open question 3. **Measure the per-image publish time in this
> unit** and record it in `progress-tracker.md`. If it does not fit the NFR-101 budget, the remedy
> is a different AMQP client, never a change to the message contract.

Map `pika` exceptions to `PublishError`, preserving the broker reply code in `amqp_ret`. Never
discard it.

### `src/fcas/publish/topology.py`

Idempotent declaration, re-run on every connect:

- Declare topic exchange `fabric.frames`, durable
- Declare topic exchange `fabric.telemetry`, durable
- Declare one durable queue per configured camera: `frames.left`, `frames.center`, `frames.right`
- Queue arguments from config: `x-max-length` (default 3), `x-overflow` = `drop-head`,
  `x-message-ttl` (default 5000)
- Bind each queue to its routing key `camera.<position>`

**Handle `PRECONDITION_FAILED` explicitly.** If a queue already exists with different arguments,
RabbitMQ closes the channel — in `pika`, a `ChannelClosedByBroker(406, ...)`. Catch it, log both
the requested and existing argument sets, and enter `DEGRADED`. Never crash-loop. This is the most
likely failure after a configuration change and must be diagnosable from one log line.

Note that a closed channel cannot be reused: recovery means opening a new channel, which is why
topology declaration and channel creation live together.

### `src/fcas/publish/message.py`

Builds the `BasicProperties` and header dict for one image, exactly per `ui-context.md`.

- All 15 headers, correctly typed. Python `int` maps to AMQP long-long and `float` to double,
  which is what the contract wants for `trigger_id`, `sequence`, `position_mm`, `exposure_us`.
  Make sure `width`, `height`, `stride` go over as integers and not as strings.
- Properties: `content_type="application/octet-stream"`, `delivery_mode=1` (transient),
  `timestamp` = capture time as int seconds, `message_id=f"{position}:{sequence}"`
- **Assert `trigger_id` and `sequence` are present and non-zero.** A message missing either is a
  defect and must raise rather than publish silently (invariant 9).
- Body is `lease.body(length)` — a `memoryview` over the pooled buffer. **Do not copy the image.**

### `src/fcas/publish/publisher.py`

Single thread, owns the connection.

- Round-robin drain across the per-camera queues so no camera starves
- Publish with confirms enabled; `basic_publish` raises `UnroutableError` / `NackError` on failure,
  which counts as a drop (FR-511)
- **Close the `ImageLease` in a `finally`** immediately after the publish call returns or the
  message is dropped — the pool must recycle promptly, and an exception path that skips the close
  drains the pool within seconds
- If the broker is unreachable, discard with reason `BROKER_UNAVAILABLE` and count it. **Never
  block acquisition** (invariant 1). Full reconnect logic is Unit 07; in this unit, failing
  cleanly and counting is sufficient.
- Track per-camera published counts for status
- **Keep heartbeats serviced.** `BlockingConnection` only processes heartbeats while inside a pika
  call, so the drain loop must not sit in a long Python-side wait. Use short queue waits (≤250 ms)
  and call `connection.process_data_events(0)` on each pass. Getting this wrong produces a
  broker-side heartbeat timeout that looks exactly like a network fault and wastes a day.

### Wiring

Replace Unit 05's temporary drain thread with the publisher. `ServiceApp.start()` constructs it
after the correlator; `stop()` tears it down before the queues so nothing is publishing into a
torn-down queue, and drains any remaining leases back to the pool.

Credentials resolve from the `env:` reference established in Unit 01 and are never logged.

### `tests/unit/test_message.py`

Header construction is pure logic — test it without a broker.

Cover: every required header present; correct Python types for each; `message_id` format;
`delivery_mode == 1`; `trigger_id` of zero rejected; `sequence` of zero rejected; correct routing
key derived from position; `stride` matches `width * 3`; the body is a `memoryview` over the
pooled buffer and not a copy (assert the buffer is not duplicated — compare object identity of
the underlying object via `memoryview.obj`).

### `tests/integration/test_publish.py`

Marked `@pytest.mark.broker`; skip cleanly with a clear message when no broker is reachable.

Cover: topology declares from nothing; re-declaring is idempotent; a published message arrives
with correct headers and body length; conflicting queue arguments produce `PRECONDITION_FAILED`
handled as designed; publishing 100 images returns the pool to full size.

### Measurement (required, not optional)

Record in `progress-tracker.md`: mean and p99 wall-clock time for `basic_publish` + confirm of one
15 MB body over loopback, over at least 200 publishes. This is the number open question 3 is
waiting for.

## Dependencies

- `pika` — add to `pyproject.toml` and re-pin `requirements.lock` in this unit, not earlier

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] `Documents/broker-setup.md` exists and a clean broker can be provisioned by following it
- [ ] Starting against an empty broker declares both exchanges and all three queues automatically
- [ ] Restarting the service re-declares without error — declaration is genuinely idempotent
- [ ] `fcas run --console --mock-cameras 2` puts messages in all three queues
- [ ] In the management UI, every message shows all 15 headers with correct values and types
- [ ] Message body length equals `width * height * 3`
- [ ] The three messages from one trigger event share a `trigger_id`
- [ ] `sequence` increments by exactly 1 per camera across consecutive messages
- [ ] Messages are transient — queue contents do not survive a broker restart
- [ ] With no consumer, queues stop at `x-max-length` and oldest messages are discarded
- [ ] Publishing with the broker stopped counts `BROKER_UNAVAILABLE` and does not block acquisition
- [ ] A queue pre-declared with wrong arguments produces a clear `PRECONDITION_FAILED` log and
      `DEGRADED`, not a crash-loop
- [ ] No heartbeat timeout occurs over a 30-minute run — confirms the drain loop services pika
- [ ] The pool returns to full size after a publish failure, not only on the success path
- [ ] `pika` is imported nowhere outside `fcas/publish`, verified by the boundary test
- [ ] Credentials appear in no log line
- [ ] **Per-image publish latency measured and recorded in `progress-tracker.md`** (open question 3)
- [ ] All tests pass, including Units 01–05
- [ ] Committed as `feat(unit-06): AMQP publisher, topology, and message contract`
