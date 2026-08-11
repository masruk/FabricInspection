# FabricInspection — Fabric Camera Acquisition Service (FCAS)

Unattended Windows service that drives three Hikrobot USB3 area-scan cameras on a textile
inspection line, captures a synchronised image set on every hardware trigger, converts to RGB8,
and publishes each image to a per-camera RabbitMQ queue for an AI inference service running on an
NVIDIA Jetson.

FCAS owns acquisition and delivery only. It performs no inference and makes no defect decisions.

- **Language:** Python 3.12 (CPython, 64-bit)
- **Camera SDK:** Hikrobot MVS Python SDK (`MvImport`, ctypes)
- **Transport:** RabbitMQ (AMQP 0-9-1) via `pika`, one queue per camera, best-effort delivery
- **Hosting:** Windows Service via `pywin32`, 24/7 unattended

## Status

Planning complete; implementation not started. See `context/progress-tracker.md`.

## Where to start

| Read | For |
| --- | --- |
| `CLAUDE.md` | Entry point and reading order |
| `context/project-overview.md` | What this is and what it is not |
| `context/architecture.md` | Boundaries, threading, memory model, invariants |
| `context/ui-context.md` | Message contract, REST API, CLI, log format |
| `context/code-standards.md` | Implementation rules |
| `context/ai-workflow-rules.md` | How work is scoped, tested, and committed |
| `context/progress-tracker.md` | Current state, open questions, decisions |
| `feature-specs/00-build-plan.md` | The 13 build units in dependency order |
| `Documents/SRS-camera-acquisition-service.md` | Numbered requirements (FR/NFR/IF/OP) |
| `Documents/SDD-camera-acquisition-service.md` | Design realisation and traceability |

The project follows the Six-File Context methodology: the six files under `context/` are the
working contract, and `Documents/` holds the deep reference they point to.

## Language change

The SRS and SDD moved from C++17 to Python at v3.0. **Only the implementation language changed.**
The message contract, topology, correlation algorithm, state machine, and all 15 acceptance
criteria are unchanged, so the consumer side is unaffected. SDD §16 maps every C++ component to
its Python counterpart, and `context/progress-tracker.md` records which v2.0 decisions were
superseded.
