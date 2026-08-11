# Unit 12: Hardware Trigger and Position Accumulation

## Goal

Switch from free-run to hardware trigger on all three cameras, verify that one trigger pulse
produces one message per camera sharing a `trigger_id`, and attach an accurate fabric position to
every message. Measure the real inter-camera skew and confirm or correct the grouping window.

## Design

This is the first unit that requires the full rig: three cameras, the trigger source, and a
running line. Everything before it was built and tested with mocks and free-run.

Two assumptions that have carried through the whole design get tested here for the first time:

- **The 200 ms grouping window** is a calculated default derived from an expected ~2800 ms trigger
  interval. It has never been checked against real skew.
- **Trigger pitch** determines `position_mm` accuracy. If the roller circumference or cam lobe
  count does not produce the assumed ~460 mm frame pitch, overlap and position both shift.

If either assumption is wrong, this unit corrects it — in the config and in the context files, not
by patching code.

## Implementation

### Trigger configuration

Extend `CameraDevice.configure_trigger()` to fully apply hardware trigger mode, using
`MV_CC_SetEnumValue` / `MV_CC_SetEnumValueByString` / `MV_CC_SetFloatValue` on the GenICam nodes:

- `TriggerMode` on
- `TriggerSource` from config (`Line0` by default)
- `TriggerActivation` — rising or falling edge from config
- `TriggerDelay` in µs from config
- `LineDebouncerTime` from config, to reject contact bounce from a proximity sensor
- Verify each node exists on the camera before setting it — `MV_CC_GetEnumValue` on an absent node
  returns an error rather than raising, so check the return and report a clear message naming the
  node if the camera does not support it. Node names vary by model; do not assume the one camera
  on hand is representative.
- Prefer `MV_CC_SetEnumValueByString("TriggerSource", "Line0")` over numeric enum values where the
  binding offers it: the string is readable in config and in logs, and numeric enum values are not
  stable across camera models.

Applied identically to all three cameras. Any camera that fails trigger configuration must not
silently fall back to free-run — that would produce untriggered frames that corrupt correlation.

### Position accumulation

- `position_mm` advances by `acquisition.triggerPitchMm` per trigger event
- `POST /roll` resets `position_mm` to zero and sets the new `roll_id` (FR-309)
- Position is assigned by the correlator at group creation, so all three images of a trigger carry
  the identical value
- Log position periodically at `INFO` — enough to correlate with the physical line, not per frame

### Skew measurement

Implement `fcas measure-skew N`: capture N trigger events and report, per camera, the distribution
of `nHostTimeStamp` offsets from the first camera in each group — min, max, mean, p99.

Note what this number actually contains: real inter-camera trigger jitter, plus USB transfer skew,
plus any delay between the SDK returning the frame and the worker thread reading its timestamp. On
a threaded runtime that last term is not zero. If measured skew comes out surprisingly large,
check whether the timestamp is the SDK's `nHostTimeStamp` (correct — set when the frame arrives)
or a `time.time()` captured in Python (wrong — includes scheduling delay). The correlator must use
the SDK's.

This produces the number that validates the grouping window. Record the measured value in
`progress-tracker.md` and resolve open question 8.

Decision rule: the grouping window must exceed measured p99 skew by at least 10×, and stay well
below the minimum trigger interval. If measured skew makes that impossible, the window needs
re-derivation and the finding goes into the SDD.

### Trigger pitch calibration

Document the calibration procedure in `Documents/commissioning-guide.md`:

1. Mark the fabric at a known start position
2. Run a known number of trigger events
3. Measure the physical distance travelled
4. Compute actual pitch = distance / trigger count
5. Set `acquisition.triggerPitchMm` to the measured value

Record the measured pitch in `progress-tracker.md` and resolve open question 7.

### Overlap verification

With the real pitch known, verify the along-web overlap is what the optical design assumed
(~15%). Capture consecutive triggers and confirm the images overlap rather than leaving a gap.

**A gap between consecutive frames means uninspected fabric on every single trigger** — a
systematic defect far more serious than an occasional dropped frame. If the measured pitch leaves
a gap, stop and escalate; it is an optical/mechanical problem, not a software one.

### Blur verification

At full line speed with the configured exposure, confirm motion blur stays within the ~0.5 px
target from CON-001. Capture images of a fabric edge or a test target at speed and inspect.

If blur exceeds the target, the exposure ceiling or the illumination is wrong. Record the finding;
do not silently raise the ceiling to make an image look brighter — that trades away defect
sharpness, which is the whole point of the system.

## Dependencies

None new. Requires: three cameras, trigger source wired in parallel to all three, running line.

## Verify when done

- [ ] `ruff`, `mypy --strict`, and `pytest` all pass
- [ ] All three cameras accept hardware trigger configuration; any failure is reported, not silently ignored
- [ ] A camera missing a trigger node produces a clear error naming the node, not a silent fallback
- [ ] One trigger pulse produces **exactly one** message per camera — no extras, no misses
- [ ] All three messages from one pulse share the same `trigger_id` (AC-04)
- [ ] 1000 consecutive triggers produce 3000 messages with no correlation errors
- [ ] `fcas measure-skew` reports the real inter-camera skew distribution
- [ ] The correlator's timestamp is confirmed to be the SDK's `nHostTimeStamp`, not a Python-side
      clock reading
- [ ] Measured p99 skew is at least 10× below the grouping window, or the window is re-derived and documented
- [ ] Trigger pitch is measured and `triggerPitchMm` set to the real value
- [ ] `position_mm` matches physical position on the roll within tolerance
- [ ] All three images of one trigger carry identical `position_mm`
- [ ] `POST /roll` resets position to zero and applies the new roll ID
- [ ] Consecutive frames **overlap** along the web — no gap (verified visually)
- [ ] Motion blur at full line speed is within the ~0.5 px target (AC-11)
- [ ] Blocking the trigger for a period produces no frames and no errors — an idle line is not a fault
- [ ] `fcasctl trigger` correctly returns `409` while hardware trigger is active
- [ ] Measured skew and pitch recorded in `progress-tracker.md`; open questions 7 and 8 resolved
- [ ] All tests pass, Units 01–11
- [ ] Committed as `feat(unit-12): hardware trigger and position accumulation`
