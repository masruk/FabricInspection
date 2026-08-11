# Development Workflow

## Approach

Build this project incrementally using a spec-driven workflow. Context files define what to build,
how to build it, and the current state of progress. `feature-specs/` defines each unit. Always
implement against these specs — do not infer or invent behavior from scratch.

Implement exactly one unit per session. Read its spec file first. Do not read ahead and do not
build ahead.

## Scoping Rules

- Work on one feature unit at a time.
- Prefer small, verifiable increments over large speculative changes.
- Do not combine unrelated system boundaries in a single implementation step.
- Do not add a dependency until the unit that first needs it.
- Do not refactor code outside the current unit's scope. If you find a problem elsewhere, record
  it in `progress-tracker.md` under Open Questions and continue.

## When To Split Work

Split an implementation step if it combines:

- Camera SDK work and AMQP publishing work
- Pipeline logic and Windows Service integration
- More than one system boundary from `architecture.md`
- Behavior that is not clearly defined in the context files or the unit spec

If a change cannot be verified quickly, the scope is too broad — split it.

## Handling Missing Requirements

- Do not invent behavior that is not defined in the context files, the unit spec, or the SRS.
- If a requirement is ambiguous, resolve it in the relevant context file before implementing.
- If a requirement is missing, add it as an open question in `progress-tracker.md` before continuing.
- If a numbered requirement in the SRS conflicts with a spec, stop and raise it. Do not silently
  pick one.

## Hardware-Dependent Work

Hardware is not always available, and only **one** physical camera exists during early development.

- Never write code that can only be tested with three physical cameras. Multi-camera behavior must
  be exercisable through `MockCameraDevice`.
- Never claim a hardware-dependent behavior is verified when it was only compiled. State plainly
  what was tested and what was not.
- Trigger-dependent and line-speed-dependent behavior cannot be verified without the rig. Mark
  those checklist items as blocked rather than checking them.
- When hardware verification is pending, record it in `progress-tracker.md` under Session Notes.

## Protected Files

Do not modify the following unless a spec explicitly requires it:

- Anything under the MVS installation directory — the Python binding in
  `…\Samples\Python\MvImport\`, the samples, the documentation. It is read-only reference. If the
  binding needs adapting, wrap it in `fcas/camera/mvs_sdk.py`; never edit the vendor file.
- `pyproject.toml` and `requirements.lock`, except in the unit that introduces the dependency
- `Documents/SRS-camera-acquisition-service.md` and `Documents/SDD-camera-acquisition-service.md` —
  these change only through a deliberate decision, never as a side effect of implementation
- Generated build and cache output

## Keeping Docs In Sync

Update the relevant context file whenever implementation changes:

- System boundaries, threading model, or invariants → `architecture.md`
- Storage, config schema, or delivery semantics → `architecture.md`
- Message contract, REST shapes, CLI output, or log format → `ui-context.md`
- Code conventions → `code-standards.md`
- Feature scope → `project-overview.md`

If a change alters a numbered requirement or a design decision, update the SRS or SDD in
`Documents/` too, and note it under Architecture Decisions in `progress-tracker.md`.

Progress state must reflect the actual state of the implementation, not the intended state.

## The Per-Unit Cycle

Every unit follows the same five steps. Do not skip or reorder them.

**1. Start**

```
Read feature-specs/NN-<name>.md.
Update context/progress-tracker.md to mark this unit in progress.
Implement it exactly as specified. Do not go beyond the scope of this unit.
```

Create the branch before writing code: `git checkout -b feat/NN-<name>`

**2. Implement** — only what the spec says. No speculative extras.

**3. Test** — run all three gates:

```bash
ruff check . && ruff format --check . && mypy --strict src tests && pytest
```

Every check must pass, including tests from earlier units. A unit that breaks an earlier unit's
tests is not complete. `mypy` and `ruff` are gates on equal footing with the tests — in a
dynamically typed language they are the only mechanical check available, and skipping them
defers real defects to the soak run.

**4. Verify** — walk the spec's checklist item by item. Check what genuinely passes. Mark
hardware-dependent items **blocked** with a reason rather than checking them. Do not check an
item you did not actually confirm.

**5. Commit** — update the tracker, then commit:

```
git add -A
git commit -m "feat(unit-NN): <short description>"
```

Commit message format: `feat(unit-NN): <what was built>`. Use `fix(unit-NN):` for corrections to
an already-committed unit. One commit per unit unless the unit is large enough that intermediate
commits genuinely help review.

Only after the commit lands do you move to the next unit.

## Before Moving To The Next Unit

1. The current unit works end to end within its defined scope.
2. Every item on the unit's verification checklist is checked, or explicitly marked blocked with
   a reason.
3. No invariant defined in `architecture.md` was violated.
4. `ruff check`, `ruff format --check`, and `mypy --strict` all pass with zero findings.
5. All tests pass — this unit's and every earlier unit's.
6. Every pipeline test asserts the buffer pool returned to full size at teardown.
7. `progress-tracker.md` reflects the completed work, not the intended work.
8. The work is committed.
