# Current Issues

Bug-fix and correction instruction files live here, one per review pass.

This folder is for problems found **after** a unit was marked complete — things that do not
warrant a new unit but must be fixed before moving on. It is not a backlog and not a place for
new features. New functionality gets a numbered spec in `feature-specs/`.

## Naming

`current-issues-<area>.md` — for example `current-issues-camera-recovery.md`,
`current-issues-publisher.md`.

## Format

Open with which files to read first and an instruction not to break existing behaviour. Then one
numbered section per issue, each stating the observed behaviour, the expected behaviour, and the
scope of the fix.

```markdown
Review the <area> implementation and fix the following issues.
Read <file paths> first. Do not break existing features.

## Issues

### 1. <Short title>

Read <specific file> before implementing.

Observed: <what happens now>
Expected: <what should happen, referencing the SRS requirement ID where one applies>

<Any specific constraints on the fix.>

Do not change anything else.
```

## Rules

- Delete the file once every issue in it is fixed and verified. This folder reflects open work
  only.
- One area per file. Do not mix camera issues with publisher issues.
- Always state the requirement ID (`FR-503`, `NFR-105`) when the issue is a requirement violation
  rather than a defect.
- If an issue turns out to be a missing requirement rather than a bug, move it to
  `progress-tracker.md` under Open Questions and resolve it in the context files first.
