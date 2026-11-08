# Hako Foundry REST API

Exposes powerboard metrics and fan control over HTTP. All endpoints require API key authentication.

## Setup

API keys are managed in the Foundry UI under **Settings > API Keys**. Click **Generate New API Key**, give it a name, and copy the key when shown as it is displayed only once.

Keys are stored as SHA-256 hashes in `config/api_config.json`. If no keys have been created, all requests will return `503 Service Unavailable`.

## Authentication

Pass your key in the `X-API-Key` request header:

```sh
curl -H "X-API-Key: hf-api_your-key-here" http://localhost:8080/api/v1/status
```

---

## Endpoints

### Service

#### `GET /api/v1/status`

Returns service health, the running app version and the number of connected powerboards.

**Response**
```json
{
  "status": "ok",
  "version": "0.8",
  "revision": "1efa593c2b7d4e8f9a0b1c2d3e4f5a6b7c8d9e0f",
  "powerboards_connected": 2,
  "powerboard_locations": [1, 2]
}
```

---

### Powerboards

#### `GET /api/v1/powerboards`

Returns metrics for all connected powerboards.

**Response**
```json
{
  "powerboards": [
    { "location": 1, ... },
    { "location": 2, ... }
  ]
}
```

#### `GET /api/v1/powerboards/{location}`

Returns metrics for a single powerboard. `location` is `1` or `2`.

**Response**
```json
{
  "location": 1,
  "hardware_revision": "...",
  "firmware_version": "...",
  "is_connected": true,
  "fan": {
    "pwm_percent":       { "row1": 60, "row2": 60, "row3": 60 },
    "saved_pwm_percent": { "row1": 50, "row2": 50, "row3": 50 },
    "rpm":               { "row1": 1200, "row2": 1180, "row3": null }
  },
  "power": {
    "shunt1_watts": 45.2,
    "shunt2_watts": 38.1,
    "shunt3_watts": null,
    "shunt4_watts": null,
    "section_1_2_watts": 83.3,
    "section_3_4_watts": null,
    "total_watts": 83
  }
}
```

`fan.pwm_percent` is the running PWM (what the fans are doing now, including any override). `fan.saved_pwm_percent` is the EEPROM value the board boots with.

> RPM and wattage fields may be `null` if the first poll hasn't completed yet.

**Errors**
| Status | Reason |
|--------|--------|
| `404`  | No powerboard at that location |

---

### Fans

#### `GET /api/v1/fans/status`

Returns PWM, RPM, and override state for all fan walls.

**Response**
```json
{
  "override_active": false,
  "walls": [
    {
      "wall_id": 1,
      "key": "1",
      "name": "Wall 1",
      "pwm_percent": 60,
      "rpm": 1200,
      "manual": false,
      "assigned_profile": "balanced",
      "override_active": false,
      "powerboard_id": 1,
      "header_index": 0
    }
  ]
}
```

#### `GET /api/v1/fans/status/{wall_key}`

Same as above, filtered to one wall. Valid `wall_key` values:

| Key | Wall |
|-----|------|
| `1` | Fan Wall 1 |
| `2` | Fan Wall 2 |
| `3` | Fan Wall 3 |
| `aux1` or `4` | Auxiliary Fan 1 |
| `aux2` or `5` | Auxiliary Fan 2 |
| `aux3` or `6` | Auxiliary Fan 3 |

**Errors**
| Status | Reason |
|--------|--------|
| `404`  | Wall not found |
| `422`  | Unknown wall identifier |
| `503`  | Fan control service unavailable |

---

#### `POST /api/v1/fans/override`

Engage or release a temporary fan speed override. Overrides set walls to manual mode without writing to the config on disk. Only walls named in an engage request (or every assigned wall for a global `pwm_percent`) are overridden and reported with `override_active=true`. Each wall's state is captured the first time it is targeted and restored on release.

The override is held in memory only: the persisted configuration is never modified by an override, and a service restart returns every wall to its configured state with `override_active=false`.

**Request body**
```json
{
  "active": true,
  "pwm_percent": 80,
  "walls": {
    "1": 90,
    "aux1": 60,
    "aux3": 50
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `active` | bool | yes | `true` to engage, `false` to release |
| `pwm_percent` | int 0–100 | no | Global speed applied to all walls not listed in `walls` |
| `walls` | object | no | Per-wall speeds. Keys: `"1"`–`"3"` (main), `"4"`/`"aux1"`, `"5"`/`"aux2"`, `"6"`/`"aux3"` (auxiliary). Values: 0–100 |

When `active=true`, at least one of `pwm_percent` or `walls` must be provided. Per-wall entries in `walls` take precedence over `pwm_percent`.

Subsequent calls with `active=true` update speeds without replacing the original snapshot; newly targeted walls are added to it.

Powerboards are written one at a time. If a hardware write fails during engage, boards already written are rolled back, wall states are restored, and the request answers `502` naming the failing powerboard location. If a write fails during release, the request answers `502` and the snapshot is kept so the release can be retried.

**Response (engaged)**
```json
{
  "override_active": true,
  "applied": { "Fan Wall 1": 90, "Auxiliary Fan 1": 60, "Fan Wall 2": 80 },
  "skipped": [{ "wall": "Auxiliary Fan 3", "reason": "no hardware assignment" }],
  "snapshot_taken": true
}
```

`override_active` reports whether an override is active after the call. `snapshot_taken` is `true` only when this call started a new override, i.e. no override was active before and at least one wall was captured. When every targeted wall is skipped (for example, none has a hardware assignment) and no override was already active, the response has `override_active=false` and `snapshot_taken=false`, and no fan speed changes.

**Response (released)**
```json
{
  "override_active": false,
  "detail": "Override released. Wall states restored."
}
```

**Errors**
| Status | Reason |
|--------|--------|
| `422`  | `active=true` with no speed targets, or invalid wall key / PWM value |
| `502`  | Powerboard write failed (engage rolled back; release keeps the snapshot) |
| `503`  | Fan control service unavailable |

**Response (502)**
```json
{
  "detail": "powerboard 2 write failed: Serial communication error: write timeout; changes rolled back"
}
```

#### Known limitations

- Changes made in the web UI to the manual flag, profile or speed of an overridden wall while the override is active are not captured and are discarded when the override is released. Non-overridden walls are unaffected.
- The override has no expiry and accepts 0 percent. A client that engages 0 percent and never releases leaves those fans stopped until release or a service restart.
- If a rollback or release write fails, the board keeps its override speeds until it responds; the 3 second control loop re-applies the wall speeds on every tick until the write succeeds.
- If Settings reassigns a wall to a different header while an override is active, release briefly writes the snapshot speed to the old header; the 3 second control loop corrects it on the next cycle.
- Using Settings "Apply PWM" on a board while an API override is active overwrites the overridden walls' live speed; the override remains flagged active and release still restores the pre-override values.

---

## Error format

All errors follow FastAPI's standard detail envelope:

```json
{ "detail": "Human-readable error message." }
```

## Authentication errors

| Status | Reason |
|--------|--------|
| `401`  | Missing or invalid `X-API-Key` |
| `503`  | No API keys configured on the server |
