# Unit 11: Integration Guide and Python Example Consumer

## Goal

Deliver everything the AI team needs to consume the stream: a documented message contract, a
runnable Python consumer that reassembles full-width slices by `trigger_id`, and demonstrated
gap detection via `sequence` discontinuity.

## Design

This unit is the actual handoff — the point where your module becomes usable by someone else.
Success is measured by whether the ML developer can consume the stream **without asking you a
question**. If they need to ask, the documentation failed.

Two things the consumer must get right, both easy to get wrong and both invisible when wrong:

1. **Grouping by `trigger_id`** — three queues deliver independently, so the consumer must join
   them itself.
2. **Gap detection via `sequence`** — RabbitMQ discards silently once it has accepted a message.
   Sequence discontinuity is the *only* signal that fabric went uninspected.

The example is reference code, not production code, and must say so.

## Implementation

### `Documents/integration-guide.md`

Written for someone who has never seen this codebase.

- **Quick start**: connect, consume, get an image in under 20 lines
- **Topology**: exchanges, queues, routing keys, and the queue arguments with an explanation of
  what `drop-head` and TTL mean for the consumer
- **Message contract**: the full header table from `ui-context.md`, with types and an example value
  for each
- **Body format**: raw RGB8, row-major, uncompressed, length `width * height * 3`, with a worked
  example of turning it into a numpy array of the right shape — including the `stride` caveat
- **Correlation**: how to group by `trigger_id`, why a group may contain fewer than three images,
  and a recommended assembly timeout
- **Gap detection**: how to track `sequence` per camera, what a discontinuity means physically
  (uninspected fabric), and what to record
- **Acknowledgement and prefetch**: ack after processing, use a small `basic.qos` prefetch (1–2)
  so images do not accumulate client-side (IF-203)
- **Position mapping**: `LEFT` / `CENTER` / `RIGHT` and how they lay out across the web
- **Failure modes**: what the consumer sees when a camera is down, when the broker restarts, and
  when FCAS is stopped
- **Requirement references** so a reader can find the authority in the SRS

### `examples/consumer.py`

Runnable reference consumer using `pika`. Single file, heavily commented, no framework.

Structure:

- Connect and declare **passively** — the consumer must never create queues; FCAS owns topology.
  Passive declaration also gives a clear error if FCAS has not run yet.
- Consume all three queues on one connection
- A `SliceAssembler` keyed by `trigger_id`, holding partial groups with a configurable timeout.
  On timeout, emit the partial group and record which positions were missing — never wait forever.
- A `GapDetector` tracking last `sequence` per camera, emitting a clear warning naming the
  missing range and the fabric position span it corresponds to
- Convert the body to a numpy array with correct shape and dtype
- Print a per-slice summary: `trigger_id`, positions present, `position_mm`, and any gap detected
- `--save` writes assembled slices to disk for visual confirmation
- Clean shutdown on Ctrl+C: cancel consumers, ack outstanding, close

Include a `requirements.txt` pinning `pika` and `numpy`.

State clearly at the top of the file that it is a reference implementation demonstrating the
contract, not production inference code.

### `examples/README.md`

How to run the example against a live service: prerequisites, how to point it at the broker, what
correct output looks like, and how to induce a gap to see detection work.

### Verification of the contract itself

Use this unit to check the contract is genuinely complete. Anything the consumer needs that is not
in a header is a contract defect — fix it in `ui-context.md`, the SRS, and `MessageBuilder`, not by
patching around it in the example.

## Dependencies

- Python 3.8+ with `pika` and `numpy` — consumer side only. Nothing new in the C++ build.

## Verify when done

- [ ] `Documents/integration-guide.md` covers topology, headers, body format, correlation, gap
      detection, ack/prefetch, and failure modes
- [ ] `examples/consumer.py` runs against a live service and prints assembled slices
- [ ] Slices assemble correctly with all three cameras present
- [ ] With one camera stopped, partial slices emit after the timeout naming the missing position —
      no hang
- [ ] Stopping the consumer, letting queues overflow, then restarting produces a **correct gap
      report** naming the missing sequence range
- [ ] Image bytes convert to a numpy array of shape `(height, width, 3)` and the image is visually
      correct — not channel-swapped, not skewed by a stride error
- [ ] `--save` writes viewable images
- [ ] The consumer never declares or modifies queues — passive declaration only
- [ ] Ctrl+C shuts the consumer down cleanly with no unacked message warnings in the broker
- [ ] **A teammate who has not seen this codebase can run the example using only the guide** — the
      real acceptance test for this unit (AC-10)
- [ ] Any header the consumer needed that was missing has been added to the contract, not worked
      around
- [ ] All C++ unit tests still pass, Units 01–10
- [ ] Committed as `feat(unit-11): integration guide and Python example consumer`
