"""
REST API for Hako Foundry

Exposes powerboard metrics over HTTP with API key authentication.
Keys are managed via Settings > API Keys and stored in config/api_config.json.

Endpoints:
  GET  /api/v1/status                    — service health and connected powerboard count
  GET  /api/v1/powerboards               — metrics for all connected powerboards
  GET  /api/v1/powerboards/{location}    — metrics for a single powerboard (1 or 2)
  GET  /api/v1/fans/status               — PWM, RPM, and override state for all fan walls
  GET  /api/v1/fans/status/{wall_id}     — same, filtered to one wall (1, 2, 3, aux1, aux2, aux3)
  POST /api/v1/fans/override             — engage or release a temporary fan speed override
"""

import logging
from typing import Dict, Optional

from fastapi import Depends, HTTPException
from fastapi.security.api_key import APIKeyHeader
from nicegui import app, run
from pydantic import BaseModel, field_validator

import globals
from api_key_manager import api_key_manager
from powerboard import PowerboardError

# Wall ID aliases for auxiliary fan walls
_WALL_ALIAS = {"aux1": 4, "aux2": 5, "aux3": 6}

# Snapshot taken on the first POST {"active": true} during a session.
# Cleared when the override is released. Never persisted to disk.
# Structure: {wall_id: {"manual": bool, "current_speed": int}}
_override_snapshot: Optional[Dict[int, Dict]] = None

logger = logging.getLogger("foundry_logger")

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(api_key: Optional[str] = Depends(_API_KEY_HEADER)) -> None:
    """FastAPI dependency: validates the X-API-Key header."""
    if not api_key_manager.list_keys():
        raise HTTPException(
            status_code=503,
            detail="API access is not enabled on this server (no API keys configured).",
        )

    if not api_key or not api_key_manager.validate_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _serialize_powerboard(location: int):
    """Return a dict of metrics for a single powerboard by location."""
    pb_dict = globals.powerboardDict or {}
    pb = pb_dict.get(location)
    if pb is None:
        return None

    # PWM percentages are always available after init
    pwm = pb.get_fan_pwm()

    # RPM and wattage may be unavailable if the first poll hasn't completed
    try:
        rpm = pb.get_fan_tach()
    except PowerboardError:
        rpm = (None, None, None)

    try:
        wattage = pb.get_power_usage()
    except PowerboardError:
        wattage = (None, None, None, None)

    total_watts = (
        sum(w for w in wattage if w is not None) if any(w is not None for w in wattage) else None
    )

    return {
        "location": location,
        "hardware_revision": pb.hardware_revision,
        "firmware_version": pb.firmware_version,
        "is_connected": pb.is_connected,
        "fan": {
            "pwm_percent": {
                "row1": pwm[0],
                "row2": pwm[1],
                "row3": pwm[2],
            },
            "rpm": {
                "row1": rpm[0],
                "row2": rpm[1],
                "row3": rpm[2],
            },
        },
        "power": {
            "shunt1_watts": float(wattage[0]) if wattage[0] is not None else None,
            "shunt2_watts": float(wattage[1]) if wattage[1] is not None else None,
            "shunt3_watts": float(wattage[2]) if wattage[2] is not None else None,
            "shunt4_watts": float(wattage[3]) if wattage[3] is not None else None,
            "section_1_2_watts": pb.watt_sec_1_2,
            "section_3_4_watts": pb.watt_sec_3_4,
            "total_watts": int(total_watts) if total_watts is not None else None,
        },
    }


@app.get("/api/v1/status", tags=["api"])
async def api_status(_: None = Depends(_require_api_key)):
    """Return service health and how many powerboards are connected."""
    pb_dict = globals.powerboardDict or {}
    return {
        "status": "ok",
        "powerboards_connected": len(pb_dict),
        "powerboard_locations": sorted(pb_dict.keys()),
    }


@app.get("/api/v1/powerboards", tags=["api"])
async def get_all_powerboards(_: None = Depends(_require_api_key)):
    """Return metrics for every connected powerboard."""
    pb_dict = globals.powerboardDict or {}
    boards = []
    for location in sorted(pb_dict.keys()):
        data = _serialize_powerboard(location)
        if data is not None:
            boards.append(data)
    return {"powerboards": boards}


@app.get("/api/v1/powerboards/{location}", tags=["api"])
async def get_powerboard(location: int, _: None = Depends(_require_api_key)):
    """Return metrics for a single powerboard by its location index (1 or 2)."""
    pb_dict = globals.powerboardDict or {}
    if location not in pb_dict:
        raise HTTPException(
            status_code=404,
            detail=f"Powerboard at location {location} not found. "
                   f"Connected locations: {sorted(pb_dict.keys())}",
        )
    return _serialize_powerboard(location)

# Reverse alias map for serializing wall IDs back to friendly names
_WALL_ID_TO_KEY = {v: k for k, v in _WALL_ALIAS.items()}  # {4: "aux1", 5: "aux2", 6: "aux3"}


def _serialize_fan_wall(wall_id: int):
    """Return a status dict for a single fan wall."""
    svc = globals.fan_control_service
    if svc is None:
        return None

    wall = svc.fan_walls.get(wall_id)
    if wall is None:
        return None

    # Look up RPM from the powerboard using the wall's header assignment
    rpm = None
    pb_dict = globals.powerboardDict or {}
    if wall.powerboard_id is not None and wall.header_index is not None:
        pb = pb_dict.get(wall.powerboard_id)
        if pb is not None and pb._current_fan_rpm is not None:
            rpm = pb._current_fan_rpm[wall.header_index]

    return {
        "wall_id": wall_id,
        "key": _WALL_ID_TO_KEY.get(wall_id, str(wall_id)),
        "name": wall.name,
        "pwm_percent": wall.current_speed,
        "rpm": rpm,
        "manual": wall.manual,
        "assigned_profile": wall.assigned_profile,
        "override_active": _override_snapshot is not None and wall_id in _override_snapshot,
        "powerboard_id": wall.powerboard_id,
        "header_index": wall.header_index,
    }


@app.get("/api/v1/fans/status", tags=["fans"])
async def get_fans_status(_: None = Depends(_require_api_key)):
    """Return PWM, RPM, and override state for all fan walls."""
    svc = globals.fan_control_service
    if svc is None:
        raise HTTPException(status_code=503, detail="Fan control service not available.")

    return {
        "override_active": _override_snapshot is not None,
        "walls": [_serialize_fan_wall(wid) for wid in sorted(svc.fan_walls.keys())],
    }


@app.get("/api/v1/fans/status/{wall_key}", tags=["fans"])
async def get_fan_wall_status(wall_key: str, _: None = Depends(_require_api_key)):
    """Return PWM, RPM, and override state for a single fan wall (1, 2, 3, aux1, aux2, or aux3)."""
    svc = globals.fan_control_service
    if svc is None:
        raise HTTPException(status_code=503, detail="Fan control service not available.")

    try:
        wall_id = _resolve_wall_key(wall_key)
    except (ValueError, KeyError):
        raise HTTPException(status_code=422, detail=f"Unknown wall identifier: '{wall_key}'")

    data = _serialize_fan_wall(wall_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Fan wall '{wall_key}' not found. "
                   f"Available: {[_WALL_ID_TO_KEY.get(w, str(w)) for w in sorted(svc.fan_walls.keys())]}",
        )
    return data


class OverrideRequest(BaseModel):
    active: bool
    pwm_percent: Optional[int] = None   # global default for all walls (0-100)
    walls: Optional[Dict[str, int]] = None  # per-wall overrides: "1"/"2"/"3"/"4"/"5"/"6"/"aux1"/"aux2"/"aux3" -> 0-100

    @field_validator("pwm_percent")
    @classmethod
    def _check_global_pwm(cls, v):
        if v is not None and not (0 <= v <= 100):
            raise ValueError("pwm_percent must be between 0 and 100")
        return v

    @field_validator("walls")
    @classmethod
    def _check_wall_pwm(cls, v):
        if v is None:
            return v
        for key, speed in v.items():
            resolved = _WALL_ALIAS.get(key, None)
            try:
                wall_id = resolved if resolved is not None else int(key)
            except ValueError:
                raise ValueError(f"Unknown wall identifier: '{key}'")
            if not (0 <= speed <= 100):
                raise ValueError(f"Speed for wall '{key}' must be between 0 and 100")
        return v


def _resolve_wall_key(key: str) -> int:
    """Convert a wall key string ('1', '2', '3', 'aux1', 'aux2', 'aux3') to an int wall ID."""
    if key in _WALL_ALIAS:
        return _WALL_ALIAS[key]
    try:
        return int(key)
    except ValueError:
        raise ValueError(f"Unknown wall identifier: '{key}'")


@app.post("/api/v1/fans/override", tags=["fans"])
async def fan_override(request: OverrideRequest, _: None = Depends(_require_api_key)):
    """
    Engage or release a temporary fan speed override.

    - active=true: force all (or specific) walls to manual mode at the given speed.
      The first call snapshots current wall state; subsequent calls with active=true
      only update speeds without altering the snapshot.
    - active=false: restore every wall to its pre-override state and clear the snapshot.

    Wall keys: "1", "2", "3" (main walls), "4"/"aux1", "5"/"aux2", "6"/"aux3" (auxiliary walls).
    Per-wall speeds in `walls` take precedence over the global `pwm_percent`.
    """
    global _override_snapshot

    svc = globals.fan_control_service
    if svc is None:
        raise HTTPException(status_code=503, detail="Fan control service not available.")

    if not request.active:
        if _override_snapshot is None:
            return {"override_active": False, "detail": "No active override to release."}

        pb_dict = globals.powerboardDict or {}
        pb_updates: Dict[int, dict] = {}

        for wall_id, saved in _override_snapshot.items():
            wall = svc.fan_walls.get(wall_id)
            if wall is None:
                continue
            wall.manual = saved["manual"]
            wall.current_speed = saved["current_speed"]

            pb_id = saved["powerboard_id"]
            h_idx = saved["header_index"]
            pb = pb_dict.get(pb_id) if pb_id is not None else None
            if pb is not None and h_idx is not None:
                if pb_id not in pb_updates:
                    pb_updates[pb_id] = {"pb": pb, "speeds": list(pb.get_running_fan_pwm())}
                pb_updates[pb_id]["speeds"][h_idx] = saved["current_speed"]

        for entry in pb_updates.values():
            pb_obj = entry["pb"]
            speeds = entry["speeds"]
            pb_obj.set_running_fan_pwm(*speeds)
            await run.io_bound(pb_obj.update_fan_speed, *speeds)

        _override_snapshot = None
        return {"override_active": False, "detail": "Override released. Wall states restored."}

    if request.pwm_percent is None and not request.walls:
        raise HTTPException(
            status_code=422,
            detail="Provide pwm_percent, walls, or both when active=true.",
        )

    # Resolve per-wall targets and validate all wall IDs exist
    per_wall: Dict[int, int] = {}
    if request.walls:
        for key, speed in request.walls.items():
            wall_id = _resolve_wall_key(key)
            if wall_id not in svc.fan_walls:
                raise HTTPException(
                    status_code=422,
                    detail=f"Unknown wall ID '{key}'. Available: {[_WALL_ID_TO_KEY.get(w, str(w)) for w in sorted(svc.fan_walls.keys())]}",
                )
            per_wall[wall_id] = speed

    # Snapshot on first engagement only
    was_new = _override_snapshot is None
    if was_new:
        _override_snapshot = {
            wall_id: {
                "manual": wall.manual,
                "current_speed": wall.current_speed,
                "powerboard_id": wall.powerboard_id,
                "header_index": wall.header_index,
            }
            for wall_id, wall in svc.fan_walls.items()
        }

    applied: Dict[str, int] = {}
    skipped: list = []
    pb_dict = globals.powerboardDict or {}
    pb_updates: Dict[int, dict] = {}

    for wall_id, wall in svc.fan_walls.items():
        # Determine target speed: per-wall entry wins, then global default
        if wall_id in per_wall:
            target = per_wall[wall_id]
        elif request.pwm_percent is not None:
            target = request.pwm_percent
        else:
            # Not covered by this request — leave untouched
            continue

        # Skip walls with no hardware assignment — nothing to drive
        if wall.powerboard_id is None or wall.header_index is None:
            skipped.append({"wall": wall.name, "reason": "no hardware assignment"})
            continue

        wall.manual = True
        wall.current_speed = target
        applied[wall.name] = target

        pb = pb_dict.get(wall.powerboard_id)
        if pb is not None:
            if wall.powerboard_id not in pb_updates:
                pb_updates[wall.powerboard_id] = {"pb": pb, "speeds": list(pb.get_running_fan_pwm())}
            pb_updates[wall.powerboard_id]["speeds"][wall.header_index] = target

    for entry in pb_updates.values():
        pb_obj = entry["pb"]
        speeds = entry["speeds"]
        pb_obj.set_running_fan_pwm(*speeds)
        await run.io_bound(pb_obj.update_fan_speed, *speeds)

    return {
        "override_active": True,
        "applied": applied,
        "skipped": skipped,
        "snapshot_taken": was_new,
    }
