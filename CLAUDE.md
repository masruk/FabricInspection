# This is a C++17 Windows Service, not a web application

This project has no web framework, no database, and no browser UI. Do not apply
web-application patterns. The deliverable is a native Windows service that drives
industrial cameras through a vendor C SDK and publishes to a message broker.

## Application Building Context

Read the following files in order before implementing or making any architectural decision:

1. `context/project-overview.md` — product definition, goals, features, and scope
2. `context/architecture.md` — system structure, boundaries, threading model, storage, and invariants
3. `context/ui-context.md` — operator and interface surface: CLI, REST shapes, log format, message contract
4. `context/code-standards.md` — implementation rules and conventions
5. `context/ai-workflow-rules.md` — development workflow, scoping rules, and delivery approach
6. `context/progress-tracker.md` — current phase, completed work, open questions, and next steps

## Authoritative reference documents

The context files above are the working contract. The following are the deep reference —
consult them when a context file points to them, and keep them in sync when a decision changes:

- `Documents/SRS-camera-acquisition-service.md` — numbered requirements (FR/NFR/IF/OP), message
  contract, acceptance criteria
- `Documents/SDD-camera-acquisition-service.md` — threading model, buffer ownership, correlation
  algorithm, component design, traceability

When a spec references a requirement ID (`FR-503`, `NFR-105`), it refers to the SRS.

## Rules

Update `context/progress-tracker.md` after each meaningful implementation change.

If implementation changes the architecture, scope, or standards documented in the context files,
update the relevant file before continuing. If it changes a numbered requirement or a design
decision, update the SRS or SDD as well.

Never mark a unit complete without its verification checklist passing.
