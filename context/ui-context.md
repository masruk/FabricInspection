# Interface & Operator Surface Context

> **Adaptation note.** The Six-File methodology uses `ui-context.md` to stop the agent inventing
> visual decisions. FCAS is a headless Windows service with no GUI, so this file serves the same
> purpose for the surfaces it actually has: the **message contract**, the **REST API**, the
> **CLI**, and the **log format**. The rule is unchanged — the agent never invents an interface
> shape, it reads it here. The filename is kept so the methodology's entry-point ordering works
> as written.

Authoritative long form: `Documents/SRS-camera-acquisition-service.md` §5.

## Naming Conventions

| Thing | Convention | Examples |
| --- | --- | --- |
| Exchange | `fabric.<domain>` | `fabric.frames`, `fabric.telemetry` |
| Queue | `<domain>.<position>` | `frames.left`, `frames.center`, `frames.right` |
| Routing key | `camera.<position>` | `camera.left`, `camera.center`, `camera.right` |
| Logical position | UPPER SNAKE in code, lower in topology | `LEFT` / `camera.left` |
| Message header | lower_snake_case | `trigger_id`, `camera_position` |
| REST path | `/api/v1/<resource>` | `/api/v1/cameras` |
| JSON field | lowerCamelCase | `exposureUs`, `triggerKind` |
| Config field | lowerCamelCase | `queueMaxLength` |
| Error code | `E_<DOMAIN>_<REASON>` | `E_CAM_OPEN_FAILED` |

Position values are exactly `LEFT`, `CENTER`, `RIGHT`. Never invent another position name.

## Message Contract

**Body:** raw RGB8, row-major, uncompressed. Length = `width x height x 3`.

**AMQP properties:** `content_type = "application/octet-stream"`, `delivery_mode = 1` (transient),
`timestamp` = capture time, `message_id = "{camera_position}:{sequence}"`.

**Headers** — every message carries all of these. A message missing `trigger_id` or `sequence`
is a defect:

| Header | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | Contract version, currently `"1.0"` |
| `trigger_id` | int64 | Shared across all cameras for one trigger event — the join key |
| `camera_position` | string | `LEFT` / `CENTER` / `RIGHT` |
| `camera_serial` | string | Physical camera identity |
| `sequence` | int64 | Per-camera monotonic counter — gap detection |
| `width` | int32 | Image width in pixels |
| `height` | int32 | Image height in pixels |
| `pixel_format` | string | `RGB8` |
| `stride` | int32 | Bytes per row |
| `host_timestamp_ns` | int64 | Host capture timestamp |
| `roll_id` | string | Current roll identifier |
| `position_mm` | double | Estimated fabric position |
| `trigger_kind` | string | `HARDWARE` / `SOFTWARE` / `FREERUN` |
| `exposure_us` | double | Capture exposure |
| `gain_db` | double | Capture gain |

**Queue arguments** (declared idempotently by FCAS, never by hand):

| Argument | Default |
| --- | --- |
| `x-max-length` | `3` |
| `x-overflow` | `drop-head` |
| `x-message-ttl` | `5000` |
| `durable` | `true` |

## State Vocabulary

Exactly these values. Used identically in REST responses, CLI output, logs, and telemetry.

| State | Meaning |
| --- | --- |
| `IDLE` | Service up, no cameras open |
| `READY` | Cameras open and configured, acquisition stopped |
| `RUNNING` | Acquiring and publishing normally |
| `DEGRADED` | Acquiring, but a camera is missing or the broker is unreachable |
| `FAULT` | Cannot operate — no cameras, invalid config, or budget exceeded |

Camera connection states: `CONNECTED`, `DISCONNECTED`, `UNMAPPED`, `ERROR`.

Drop reasons: `BROKER_UNAVAILABLE`, `LOCAL_QUEUE_FULL`, `POOL_EXHAUSTED`, `CAMERA_MISSING`.

## REST API

Base path `/api/v1`. All requests and responses are JSON. Bind to the management interface only.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/cameras` | List cameras and connection state |
| `GET` | `/cameras/{position}/config` | Get camera settings |
| `PUT` | `/cameras/{position}/config` | Update camera settings |
| `POST` | `/acquisition/start` | Start acquisition |
| `POST` | `/acquisition/stop` | Stop acquisition |
| `POST` | `/acquisition/trigger` | Software trigger |
| `POST` | `/roll` | Set roll ID and reset position |
| `GET` | `/status` | Full system status |
| `GET` | `/health` | Liveness and readiness |

**Every response uses this envelope.** Do not invent alternative shapes:

```json
{ "ok": true,  "code": 0,   "message": "OK",              "data": { } }
{ "ok": false, "code": 1203, "message": "Camera LEFT is not connected", "data": null }
```

HTTP status codes: `200` success, `400` invalid input, `404` unknown resource,
`409` invalid state for the operation, `500` internal failure. The `code` field carries the
numeric FCAS error code; `0` means success.

## CLI (`fcasctl`)

Thin client over the REST API. It contains no logic of its own.

```
fcasctl status
fcasctl cameras
fcasctl config get LEFT
fcasctl config set LEFT --exposure-us 700 --gain-db 6.0
fcasctl start
fcasctl stop
fcasctl trigger
fcasctl roll set R-2026-0801-17
```

Output conventions:

- Default output is a human-readable aligned table.
- `--json` on any command emits the raw response envelope for scripting.
- Exit code `0` on success, `1` on operation failure, `2` on usage error.
- Errors print to stderr as `error: <message>`; never to stdout.
- No colour codes, no spinners, no progress bars — output may be captured to a log.

Example `fcasctl status`:

```
State        RUNNING
Uptime       4d 02:17:33
Roll         R-2026-0801-17
Broker       connected (127.0.0.1:5672)
Triggers     132847      Rate 0.36/s
Published    398541      Dropped 0

POSITION  SERIAL      STATE       SEQUENCE  LAST ERROR
LEFT      DB0717739   CONNECTED     132847  -
CENTER    DB0717740   CONNECTED     132847  -
RIGHT     DB0717741   CONNECTED     132846  -
```

## Log Format

One line per event. Fields are ordered and stable so logs stay greppable.

```
2026-08-02 14:33:07.412 [INFO ] [camera ] LEFT DB0717739 connected
2026-08-02 14:33:07.598 [INFO ] [service] state IDLE -> READY (cameras opened: 3)
2026-08-02 14:33:12.004 [WARN ] [publish] drop reason=BROKER_UNAVAILABLE position=LEFT sequence=88215
2026-08-02 14:33:12.005 [ERROR] [publish] connect failed amqp=-9 (socket closed) retry in 1s
```

- Timestamp with milliseconds, level padded to five characters, component tag padded to seven.
- Component tags: `service`, `config `, `camera `, `pipelin`, `publish`, `control`, `health `.
- State transitions always log old state, new state, and the cause in parentheses.
- Drops always log `reason=`, `position=`, and `sequence=`.
- Vendor failures always include the raw code (`sdk=0x80000004`, `amqp=-9`).
- Nothing is logged per-frame above `DEBUG`.

## Telemetry Message

JSON published to `fabric.telemetry` with routing key `status`, default every 5 s. Field names
match the `GET /status` response exactly — the same serializer produces both.
