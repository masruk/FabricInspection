# Fabric Camera Acquisition Service (FCAS)

## Overview

FCAS is an unattended Windows service, written in Python 3.12, running on a Hikrobot vision box
in a textile mill. It drives three USB3 area-scan cameras that image fabric moving on an
inspection line, captures a synchronised set of images on every hardware trigger, converts them
to RGB8, and publishes them to RabbitMQ where an AI inference service on an NVIDIA Jetson
consumes them to detect defects. FCAS owns image acquisition and delivery only — it performs no
inference and makes no defect decisions.

## Goals

1. Capture one image from each of three cameras on every hardware trigger, with a shared
   correlation ID so the consumer can reassemble a full-width fabric slice.
2. Publish images to a per-camera RabbitMQ queue within 300 ms (p99) of the trigger.
3. Run 24/7 unattended, surviving camera disconnects, broker outages, and reboots without
   operator intervention.
4. Hold memory bounded and stable — under 5% growth over 7 days of continuous operation.
5. Let an operator configure every camera parameter from a CLI or UI without restarting the service.
6. Make every discarded frame detectable, so uninspected fabric is never silently lost.

## Core Operational Flow

1. Vision box boots; Windows starts FCAS automatically with no operator login.
2. FCAS loads and validates its configuration file; invalid config is fatal and refuses to start.
3. FCAS initialises the MVS Python SDK, checks its memory budget, and pre-allocates the image
   buffer pool.
4. FCAS enumerates USB3 cameras and maps each serial number to a logical position: LEFT, CENTER, RIGHT.
5. FCAS applies each camera's configuration profile and arms hardware trigger mode.
6. FCAS connects to the RabbitMQ broker and declares its exchange and per-camera queues idempotently.
7. Operator starts acquisition via CLI, or it auto-starts per configuration.
8. A proximity sensor or encoder on the line fires a trigger pulse to all three cameras in parallel.
9. Each camera exposes and delivers a Bayer frame; FCAS debayers it to RGB8 in the pooled buffer.
10. FCAS groups the three frames into one trigger event and assigns a shared `trigger_id` plus a
    per-camera monotonic `sequence`.
11. FCAS publishes each image to its own queue with full metadata headers.
12. The Jetson consumer reads all three queues, groups by `trigger_id`, and runs inference.
13. Steps 8–12 repeat continuously for the life of the roll.
14. On roll change, the operator resets the roll ID and position counter via CLI.

## Features

### Camera Management

- Automatic enumeration and hot-plug detection of USB3 Hikrobot cameras.
- Identity by serial number mapped to a fixed logical position — never by USB port order.
- Per-camera configuration: ROI, exposure, gain, contrast, gamma, white balance, Bayer quality.
- Automatic reconnection with exponential backoff when a camera drops.
- Continues operating on remaining cameras when one fails.

### Triggering and Acquisition

- Hardware trigger as the production mode, wired in parallel to all three cameras.
- Software trigger for commissioning and testing.
- Free-run mode with configurable FPS for focus and lighting setup.
- Enforced exposure ceiling to prevent motion blur at line speed.
- Trigger correlation by host-timestamp window, assigning one shared `trigger_id` per event.
- Fabric position accumulation from trigger count and configured trigger pitch.

### Publishing

- One durable RabbitMQ queue per camera, bound to a topic exchange.
- Broker-side queue limiting: `x-max-length` with `drop-head`, plus message TTL for stale frames.
- Transient (non-persistent) messages — image data is intentionally discardable.
- Publisher confirms; unconfirmed publishes counted as drops.
- Automatic broker reconnection with idempotent topology redeclaration.
- Per-camera monotonic sequence numbers so the consumer can detect discarded frames.

### Operations

- Local REST API and `fcasctl` CLI for configuration, control, and status.
- Rotating file logs plus Windows Event Log for lifecycle events.
- Periodic telemetry published to a separate broker exchange.
- Internal watchdog that detects and recovers a stalled acquisition loop.
- Optional diagnostic mode that writes captured images to disk.

## Scope

### In Scope

- USB3 Hikrobot camera control via the MVS Python SDK on Windows
- Hardware, software, and free-run triggering
- Bayer to RGB8 conversion on the vision box
- Trigger correlation and shared ID assignment
- RabbitMQ publishing with per-camera queues
- Camera and broker failure recovery
- Windows Service lifecycle, auto-start, and self-recovery
- REST + CLI configuration and control
- Logging, metrics, telemetry, and health monitoring
- Message contract documentation and an example consumer
- Offline-installable pinned deployment (virtual environment + Windows Service registration)

### Out Of Scope

- Defect detection, inference, or any machine learning
- GPIO fault signalling (owned by the Jetson)
- Illumination hardware control
- Cloud upload of images
- Image storage, archival, or a database of any kind
- Operator GUI beyond the CLI and REST API
- Camera calibration or lens/optics selection
- GigE, CoaXPress, or Camera Link transports
- Line-scan camera support
- Guaranteed delivery, store-and-forward, or replay of missed frames

## Success Criteria

1. The service auto-starts on boot with no login and reaches READY with three cameras connected.
2. A hardware trigger produces exactly one message per camera, all carrying the same `trigger_id`.
3. Unplugging and replugging any camera recovers to RUNNING within 30 seconds without a restart.
4. Stopping the broker mid-run leaves acquisition running and drops counted; restarting the broker
   resumes publishing with no service restart.
5. The Jetson consumer reads all three queues and reassembles full-width slices using only the
   published message contract.
6. A consumer can detect every discarded frame through `sequence` discontinuity.
7. Trigger-to-broker latency stays at or below 300 ms at p99.
8. Memory growth stays below 5% over a 7-day continuous run.
9. Camera settings changed from the CLI take effect without restarting the service.
