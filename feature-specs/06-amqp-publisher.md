# Unit 06: AMQP Publisher — Connection, Topology, Publishing

## Goal

Connect to RabbitMQ, declare the exchange and per-camera queues idempotently, and publish each
stamped image to its own queue with the complete header set. Images become visible and correct in
the RabbitMQ management UI.

## Design

`rabbitmq-c` connections are **not thread-safe**. A single publisher thread owns the connection
and it is never touched from another thread. That thread confinement is why no locking is needed
around the AMQP client (`architecture.md`, threading model).

AMQP headers are included only within `src/publish` — no other layer sees an `amqp_*` symbol.

The queue arguments are what implement the AI team's "flush when over ML capacity" requirement.
They are declared by FCAS, not configured by hand on the broker, so a fresh broker needs no manual
provisioning (OP-107).

## Implementation

### Broker prerequisite

RabbitMQ must be installed and running locally. Document the exact steps in
`Documents/broker-setup.md`: install Erlang, install RabbitMQ, enable the management plugin,
create the `fcas` user with permissions scoped to the exchange and queues, delete or disable the
default `guest` account, and set `vm_memory_high_watermark` (OP-108).

> **Open question 2 blocks this unit.** RabbitMQ + Erlang on Windows IoT is unverified. Confirm
> installability on the vision box before relying on the local-broker design. If it cannot run
> there, escalate — it invalidates invariant 1's justification and the broker must move hosts.

### `src/publish/AmqpConnection`

RAII wrapper over `amqp_connection_state_t`.

- Connect, open a channel, authenticate, close and destroy in the destructor
- Connection tuning per the SDD: `frame_max` 1 MB (the 128 KB default would chop each 15 MB image
  into ~118 frames), `heartbeat` 10 s, `channel_max` 4
- Every `amqp_rpc_reply_t` is inspected and mapped to a `Status` preserving the raw code
- Not copyable, not movable

### `src/publish/Topology`

Idempotent declaration, re-run on every connect:

- Declare topic exchange `fabric.frames`, durable
- Declare topic exchange `fabric.telemetry`, durable
- Declare one durable queue per configured camera: `frames.left`, `frames.center`, `frames.right`
- Queue arguments from config: `x-max-length` (default 3), `x-overflow` = `drop-head`,
  `x-message-ttl` (default 5000)
- Bind each queue to its routing key `camera.<position>`

**Handle `PRECONDITION_FAILED` explicitly.** If a queue already exists with different arguments,
RabbitMQ closes the channel. Catch it, log both the requested and existing argument sets, and
enter `DEGRADED`. Never crash-loop. This is the most likely failure after a configuration change
and must be diagnosable from one log line.

### `src/publish/MessageBuilder`

Builds the AMQP properties and header table for one image, exactly per `ui-context.md`.

- All 15 headers, correctly typed — `trigger_id` and `sequence` as int64, `position_mm` and
  `exposure_us` as double, the rest as string or int32
- Properties: `content_type` = `application/octet-stream`, `delivery_mode` = 1 (transient),
  `timestamp`, `message_id` = `{position}:{sequence}`
- **Assert `trigger_id` and `sequence` are present and non-zero.** A message missing either is a
  defect and must fail loudly in Debug rather than publish silently (invariant 9).
- Body is an `amqp_bytes_t` pointing at the pooled buffer. **Do not copy the image.**

### `src/publish/AmqpPublisher`

Single thread, owns the connection.

- Round-robin drain across the per-camera queues so no camera starves
- Publish with publisher confirms enabled; wait for the confirm and treat an unconfirmed publish
  as a drop (FR-511)
- Release the `ImageLease` immediately after the publish call returns or the message is dropped —
  the pool must recycle promptly
- If the broker is unreachable, discard with reason `BROKER_UNAVAILABLE` and count it. **Never
  block acquisition** (invariant 1). Full reconnect logic is Unit 07; in this unit, failing
  cleanly and counting is sufficient.
- Track per-camera published counts for status

### Wiring

Replace Unit 05's temporary drain thread with `AmqpPublisher`. `ServiceApp::start()` constructs it
after the correlator; `stop()` tears it down before the queues so nothing is publishing into a
destroyed queue.

Credentials resolve from the `env:` reference established in Unit 01 and are never logged.

### `tests/unit/message_builder_test.cpp`

Header construction is pure logic — test it without a broker.

Cover: every required header present; correct AMQP types; `message_id` format; transient delivery
mode; `trigger_id` of zero rejected; `sequence` of zero rejected; correct routing key derived from
position; `stride` matches `width * 3`.

### `tests/integration/publish_test.cpp`

Requires a local broker; skip cleanly with a clear message when unavailable.

Cover: topology declares from nothing; re-declaring is idempotent; a published message arrives
with correct headers and body length; conflicting queue arguments produce `PRECONDITION_FAILED`
handled as designed.

## Dependencies

- `librabbitmq` (rabbitmq-c) via vcpkg — add to `vcpkg.json` in this unit, not earlier

## Verify when done

- [ ] Solution builds Debug and Release x64 with no new warnings
- [ ] `Documents/broker-setup.md` exists and a clean broker can be provisioned by following it
- [ ] Starting against an empty broker declares both exchanges and all three queues automatically
- [ ] Restarting the service re-declares without error — declaration is genuinely idempotent
- [ ] `Fcas.exe --console --run --mock-cameras 2` puts messages in all three queues
- [ ] In the management UI, every message shows all 15 headers with correct values and types
- [ ] Message body length equals `width * height * 3`
- [ ] The three messages from one trigger event share a `trigger_id`
- [ ] `sequence` increments by exactly 1 per camera across consecutive messages
- [ ] Messages are transient — queue contents do not survive a broker restart
- [ ] With no consumer, queues stop at `x-max-length` and oldest messages are discarded
- [ ] Publishing with the broker stopped counts `BROKER_UNAVAILABLE` and does not block acquisition
- [ ] A queue pre-declared with wrong arguments produces a clear `PRECONDITION_FAILED` log and `DEGRADED`, not a crash-loop
- [ ] No AMQP header is included outside `src/publish`
- [ ] Credentials appear in no log line
- [ ] All unit tests pass, including Units 01–05
- [ ] Committed as `feat(unit-06): AMQP publisher, topology, and message contract`
