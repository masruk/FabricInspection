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

Emit as a CSV or line-oriented log for offline analysis. Instrumentation must be cheap enough that
enabling it does not itself change the measurement — verify by comparing throughput with it on and
off.

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

- **FCAS growth must be under 5%** measured from post-warm-up baseline, not from process start
- Broker memory must stay bounded by the queue limits
- Plot both; a slow linear climb is a leak even if it stays under 5% for 7 days, and must be
  investigated rather than accepted

If a leak appears, bisect by unit — the per-unit commit history makes this tractable.

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

- Test environment: hardware, versions, config used
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
- [ ] 2 triggers/s per camera sustained 1 hour with zero local drops
- [ ] CPU utilisation under load recorded and within NFR-104
- [ ] 24 h soak: zero unexplained drops, unbroken sequence continuity
- [ ] 7 day memory: FCAS growth under 5% from post-warm-up baseline
- [ ] Broker memory bounded by queue limits throughout
- [ ] Every row of the fault injection matrix executed and recorded
- [ ] Reboot test passed 5 consecutive times
- [ ] `Documents/validation-report.md` complete with evidence for every acceptance criterion
- [ ] Any failed criterion is either fixed or recorded as an accepted risk with rationale
- [ ] All unit tests pass, Units 01–12
- [ ] Committed as `feat(unit-13): soak and performance validation`
