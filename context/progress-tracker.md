# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- Planning complete. Implementation not started.

## Current Goal

- Unit 01 — Project skeleton, configuration, logging, and console mode.

## Completed

- SRS v2.0 (`Documents/SRS-camera-acquisition-service.md`) — requirements, message contract,
  acceptance criteria.
- SDD v2.0 (`Documents/SDD-camera-acquisition-service.md`) — threading model, buffer ownership,
  correlation algorithm, component design.
- Six-file context system established under `context/`.
- Build plan (`feature-specs/00-build-plan.md`) — 13 units in dependency order.
- All 13 unit specs written and available for review before coding starts.

## In Progress

- None.

## Next Up

- Unit 01 — Project skeleton, configuration, logging, console mode.
- Unit 02 — MVS SDK wrapper and camera enumeration.
- Unit 03 — Single-camera capture, debayer, buffer pool.

## Open Questions

1. **Vision box RAM unknown.** MV-VC3501X-128G60 must host FCAS + RabbitMQ + Erlang VM.
   Default profile needs ~550–580 MB combined; constrained profile ~370 MB. Determines default
   queue depths and pool size. *Blocks: final config defaults.*
2. **RabbitMQ + Erlang on Windows IoT not yet verified.** If it cannot run on the vision box, the
   broker must move to another host, which changes the "publishing is local" assumption behind
   invariant 1. *Blocks: Unit 06.*
3. **USB3 controller topology unknown.** Whether the three cameras share one root hub, and the
   port power budget. May require staggered camera start. *Blocks: Unit 04 tuning.*
4. **Trigger pitch not confirmed.** Roller circumference / cam lobe count versus the required
   ~460 mm frame pitch. *Blocks: Unit 12 position accuracy.*
5. **Inter-camera skew not measured.** The 200 ms grouping window is a calculated default, not an
   observed value. Must be validated on real hardware. *Blocks: Unit 12 verification.*
6. **`x-message-ttl` value not derived.** Must come from camera-to-marking-station distance
   divided by line speed. Currently a placeholder 5000 ms. *Blocks: Unit 06 defaults.*
7. **ML team's AMQP client library and language not confirmed.** Affects the example consumer in
   Unit 11 only; the contract itself is language-neutral.
8. **RGB8 confirmed as the training format?** ASM-005 assumes the model is trained on images
   equivalent to MVS debayer output. Needs explicit confirmation from the AI team.

## Architecture Decisions

- **Build system is Visual Studio (`.sln` + `.vcxproj`, MSBuild), not CMake.** Team decision.
  Four projects: `FcasCore` static library holding all implementation, `Fcas` executable holding
  only `main.cpp`, `FcasCtl` for the CLI, `FcasTests` for GoogleTest. Tests link the real library
  rather than recompiling sources. x64 only; Win32 is not a supported platform.
- **Dependencies come from vcpkg in manifest mode with Visual Studio integration.** Keeps a single
  declarative dependency list without introducing CMake. *Confirm during review — the alternative
  is vendoring the header-only libraries directly and building rabbitmq-c separately.*
- **Transport is RabbitMQ, not gRPC.** Changed at the AI team's request; SRS/SDD revised to v2.0.
  Per-camera queues with broker-side `drop-head` limiting replace bundled Frame Sets.
- **Broker runs on the vision box.** Publishing must be a local operation so that acquisition can
  never block on network I/O (invariant 1). A remote broker would put the network in the
  acquisition path.
- **Correlation by host-timestamp window, not frame counters.** Frame-counter correlation
  desynchronises permanently and silently after a single missed trigger. Timestamp windowing is
  unambiguous because the trigger interval is ~3 orders of magnitude larger than skew. Frame
  counters are retained as a diagnostic only.
- **Images are stamped and published immediately, never buffered for set completeness.** With
  per-camera queues there is nothing to assemble on the sender side; the consumer groups by
  `trigger_id`. This removed the partial-set timeout entirely.
- **Per-camera `sequence` header is mandatory.** Once a broker sits in the path, FCAS cannot see
  messages RabbitMQ discards after accepting them. Sequence discontinuity is the only way the
  consumer can detect uninspected fabric.
- **Messages are transient, not persistent.** Persisting ~16 MB/s of intentionally-discardable
  image data would burn disk write endurance for no benefit.
- **Buffer pool is pre-allocated at startup and never grows.** This is the mechanism for bounded
  24/7 memory, not an optimisation.
- **Single publisher thread.** `rabbitmq-c` connections are not thread-safe; thread confinement
  removes all locking around the AMQP client.
- **`ICameraDevice` interface exists purely for testability.** Only one physical camera is
  available during development, so three-camera behaviour must be exercisable with mocks.

## Session Notes

- Hardware on hand: one Hikrobot **MV-CA050-12UC** (serial `DB0717739`), USB3, 2448x2048 colour.
  Verified working on the development PC via the MVS SDK.
- MVS SDK is installed at `C:\Program Files (x86)\MVS\Development`; `MVCAM_COMMON_RUNENV` is set
  and the runtime DLL directories are already on `PATH`.
- A working reference app exists outside this repo at `C:\Users\Administrator\Documents\MvCamApp\`
  — enumerate, open, grab, live-view, save. Useful as a known-good MVS call sequence for Units 02–03.
- Toolchain present: Visual Studio Community 2026, MSBuild 18.8, MSVC v145 toolset.
- Units 01–07, 10, 11 can be built with mocks and a local broker. Units 04 and 12 need real
  hardware. Unit 12 needs the trigger rig and a running line.
- The three production cameras are not yet on hand — only serial `DB0717739` is known. The other
  two entries in the config are placeholders.
