# Unit 13: Soak and Performance Validation

## Goal

Prove the service meets its non-functional requirements: latency, throughput headroom, memory
stability over days, and correct behaviour under injected faults. This is the unit that decides
whether the system is production-ready.

## Design

Everything up to here proved features work. This unit proves they keep working — which is a
different question and the one that matters for a service that must run unattended for months.

Findings here are **results, not opinions**. A soak that fails is not a reason to lower the bar;
it is a defect to fix or an explicitly accepted risk recorded in `progress-tracker.md`.

## Implementation

### Instrumentation

Add optional high-resolution timestamps at each stage boundary, enabled by config and off by
default:

- Trigger received / frame returned by SDK
- Debayer complete
- Correlator stamped
- Enqueued
- Publish called
- Broker confirm received

Use `time.perf_counter_ns()` for stage timings — it is monotonic and has the resolution this
needs. `time.time()` is not monotonic and will produce negative durations across a clock
adjustment.

Emit as a CSV or line-oriented log for offline analysis. Instrumentation must be cheap enough that
enabling it does not itself change the measurement — verify by comparing throughput with it on and
off. Accumulate into a preallocated structure and write in batches; a per-stage `log.debug()` call
with formatting on the hot path is itself measurable in this language.

### GIL and threading validation (new in the Python port)

SDD §4.5 asserts that per-camera threads overlap genuinely because `ctypes` releases the GIL
during SDK calls. This unit is where that assertion is confirmed or refuted with numbers.

- Record, per camera, the wall-clock time spent inside `MV_CC_ConvertPixelTypeEx` and the
  wall-clock span during which two or more cameras were simultaneously inside it. Genuine overlap
  confirms the analysis.
- Compare total CPU time against wall-clock time across the process: if the ratio never exceeds
  ~1.0 core under load, the threads are serialising and the analysis is wrong.
- If they do serialise, record it as a finding in `Documents/validation-report.md` and raise it —
  the remedy is an architectural decision (a debayer worker process, or moving conversion to the
  consumer), not a tweak.

### Latency measurement (NFR-101, AC-07)

Measure trigger-to-broker-accept across at least 10 000 trigger events at nominal rate.

Report min, mean, p50, p95, p99, max. **p99 must be at or below 300 ms.**

Break the total down by stage so a failure points at the responsible component rather than
requiring a fresh investigation.

### Throughput headroom (NFR-103, AC-09)

Drive 2 triggers/s per camera — roughly 5× nominal — using mock cameras or an accelerated trigger
source. Sustain for at least 1 hour.

**Zero local drops** is the pass condition. Record CPU utilisation, queue depths, and pool free
count under load. Queue depth consistently near capacity means the margin is thinner than the
headline number suggests, and that finding matters more than the pass itself.

### 24-hour soak (AC-05)

Nominal rate, all cameras, broker up, consumer running. Pass conditions:

- Zero unexplained drops
- No state transitions other than expected ones
- No error-level log entries other than injected ones
- Sequence continuity unbroken across the entire run

### 7-day memory soak (NFR-105, AC-08)

Sample FCAS RSS **and** RabbitMQ/Erlang memory at a fixed interval for 7 days.

- **FCAS growth must be under 5%** measured from post-warm-up baseline, not from process start.
  The distinction matters more here than it did in C++: the interpreter's allocator settles over
  the first minutes and measuring from process start would report growth that is not a leak.
- Broker memory must stay bounded by the queue limits
- Plot both; a slow linear climb is a leak even if it stays under 5% for 7 days, and must be
  investigated rather than accepted

Sample three additional series alongside RSS, because in this runtime they distinguish the kinds
of leak that look identical from the outside:

- **Pool free count.** A downward trend is a leaked lease and nothing else. This is the single most
  informative number in the run.
- **`len(gc.get_objects())` or `gc.get_count()`** at a coarse interval. Steady object growth with
  flat RSS means a Python-object leak that has not yet forced the heap to grow — a real defect,
  caught early.
- **Thread count** (`threading.active_count()`). A climbing count means workers are being replaced
  without being joined during hot-plug recovery.

If a leak appears, bisect by unit — the per-unit commit history makes this tractable — and use
`tracemalloc` snapshots taken hours apart to localise it.

### Fault injection matrix

Each fault, its expected behaviour, and the actual result:

| Fault | Expected |
| --- | --- |
| Unplug one camera | `DEGRADED`, others keep publishing (AC-12) |
| Unplug all cameras | `FAULT`, clean recovery on replug |
| Stop broker | `DEGRADED`, acquisition continues, drops counted (AC-06) |
| Restart broker | `RUNNING`, topology redeclared, publishing resumes |
| Stop consumer | Queues reach `x-max-length`, oldest discarded, broker memory bounded (AC-13) |
| Restart consumer | Resumes with newest frames, gap detected via `sequence` (AC-14) |
| Kill FCAS process | SCM restarts per recovery policy |
| Reboot the box | Auto-starts, no login, reaches `READY` (AC-01) |
| Fill the log disk | Logging degrades, acquisition continues, alarm raised |
| Corrupt the config, restart | Clean `FAULT`, Event Log entry, no crash loop |
| Network cable pulled | Consumer disconnects, queues bound, recovery on reconnect |

Every row must be executed and recorded — not reasoned about.

### `Documents/validation-report.md`

The deliverable. Contents:

- Test environment: hardware, Python and package versions from `requirements.lock`, MVS SDK
  version, RabbitMQ and Erlang versions, config used
- Latency results with the stage breakdown
- Throughput results with resource utilisation
- 24 h soak result
- 7 day memory result with the plot
- Fault injection matrix with actual outcomes
- Every acceptance criterion AC-01 to AC-15 marked pass, fail, or blocked with evidence
- Known limitations and accepted risks

This document is what a stakeholder reads to decide the system can go on the line.

## Dependencies

None new. Requires the full rig, a running consumer, and a machine that can be left undisturbed
for 7 days.

## Verify when done

- [ ] Instrumentation adds no measurable throughput cost when enabled
- [ ] Latency p99 at or below 300 ms across 10 000+ triggers, with stage breakdown recorded
- [ ] The `pika` publish stage is broken out separately, closing open question 3
- [ ] 2 triggers/s per camera sustained 1 hour with zero local drops
- [ ] CPU utilisation under load recorded and within NFR-104
- [ ] **Camera threads shown to overlap under load** — SDD §4.5 confirmed or refuted with numbers
- [ ] 24 h soak: zero unexplained drops, unbroken sequence continuity
- [ ] 7 day memory: FCAS growth under 5% from post-warm-up baseline
- [ ] Pool free count flat across the 7-day run — no downward trend
- [ ] Python object count and thread count flat across the 7-day run
- [ ] Broker memory bounded by queue limits throughout
- [ ] Every row of the fault injection matrix executed and recorded
- [ ] Reboot test passed 5 consecutive times
- [ ] `Documents/validation-report.md` complete with evidence for every acceptance criterion
- [ ] Any failed criterion is either fixed or recorded as an accepted risk with rationale
- [ ] All tests pass, Units 01–12
- [ ] Committed as `feat(unit-13): soak and performance validation`
