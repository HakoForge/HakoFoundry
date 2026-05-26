"""
Prometheus metrics endpoint for Hako Foundry.

The endpoint reports the readings already collected by the application. It does
not poll disks, sensors, or serial devices during a scrape.
"""

import math
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**labels: Any) -> str:
    cleaned = {key: value for key, value in labels.items() if value is not None}
    if not cleaned:
        return ""
    parts = [f'{key}="{_escape_label_value(value)}"' for key, value in cleaned.items()]
    return "{" + ",".join(parts) + "}"


def _to_prom_value(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _metric_line(metric_name: str, value: Any, **labels: Any) -> Optional[str]:
    prom_value = _to_prom_value(value)
    if prom_value is None:
        return None
    return f"{metric_name}{_labels(**labels)} {prom_value:g}"


def _append_metric(lines: List[str], metric_name: str, value: Any, **labels: Any) -> None:
    line = _metric_line(metric_name, value, **labels)
    if line is not None:
        lines.append(line)


def _append_family(lines: List[str], name: str, help_text: str, metric_type: str = "gauge") -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")


def _timestamp(value: Any) -> Optional[float]:
    if isinstance(value, datetime):
        return value.timestamp()
    return None


def _safe_call(callable_obj: Any) -> Optional[Any]:
    try:
        return callable_obj()
    except Exception:
        return None


def _iter_indexed(values: Optional[Iterable[Any]], labels: Iterable[str]) -> Iterable[tuple[str, Any]]:
    if values is None:
        return []
    return zip(labels, values)


def _collect_temperature_sensor_metrics(lines: List[str], globals_module: Any) -> None:
    service = getattr(globals_module, "temp_sensor_service", None)
    if service is None:
        return

    sensor_groups = _safe_call(service.get_sensor_groups) or {}
    _append_metric(lines, "hako_foundry_temperature_sensor_groups", len(sensor_groups))

    for group_name, group in sensor_groups.items():
        enabled = getattr(group, "enabled", True)
        _append_metric(lines, "hako_foundry_temperature_group_enabled", enabled, group=group_name)

        for sensor_name, sensor in getattr(group, "sensors", {}).items():
            labels = {
                "group": group_name,
                "sensor": sensor_name,
                "path": getattr(sensor, "hardware_path", None),
            }
            _append_metric(lines, "hako_foundry_temperature_celsius", getattr(sensor, "temperature", None), **labels)
            _append_metric(lines, "hako_foundry_temperature_min_celsius", getattr(sensor, "min_temp", None), **labels)
            _append_metric(lines, "hako_foundry_temperature_max_celsius", getattr(sensor, "max_temp", None), **labels)
            _append_metric(lines, "hako_foundry_temperature_sensor_enabled", getattr(sensor, "enabled", True), **labels)
            _append_metric(lines, "hako_foundry_temperature_sensor_hardware_available", _safe_call(sensor.is_hardware_available), **labels)
            _append_metric(lines, "hako_foundry_temperature_last_updated_timestamp_seconds", _timestamp(getattr(sensor, "last_updated", None)), **labels)

    last_update = getattr(service, "last_update", None)
    _append_metric(lines, "hako_foundry_temperature_service_last_update_timestamp_seconds", _timestamp(last_update))


def _collect_drive_metrics(lines: List[str], globals_module: Any) -> None:
    drives = getattr(globals_module, "drivesList", None) or {}
    _append_metric(lines, "hako_foundry_drives", len(drives))

    for drive_hash, drive in drives.items():
        labels = {
            "drive_hash": drive_hash,
            "model": getattr(drive, "model", None),
            "serial": getattr(drive, "serial_num", None),
            "protocol": getattr(drive, "protocol", None),
        }
        _append_metric(lines, "hako_foundry_drive_info", 1, firmware=getattr(drive, "firmware_ver", None), capacity=getattr(drive, "capacity", None), **labels)
        _append_metric(lines, "hako_foundry_drive_temperature_celsius", getattr(drive, "temp", None), **labels)
        _append_metric(lines, "hako_foundry_drive_power_cycles_total", getattr(drive, "power_cycle", None), **labels)
        _append_metric(lines, "hako_foundry_drive_rotation_rate_rpm", getattr(drive, "rotate_rate", None), **labels)

    service = getattr(globals_module, "temp_sensor_service", None)
    if service is None:
        return

    monitors = _safe_call(service.get_all_drive_monitors) or {}
    _append_metric(lines, "hako_foundry_drive_temperature_monitors", len(monitors))
    for curve_id, monitor in monitors.items():
        labels = {
            "curve_id": curve_id,
            "monitor": getattr(monitor, "name", None),
            "aggregation": getattr(monitor, "aggregation_mode", None),
        }
        _append_metric(lines, "hako_foundry_drive_monitor_temperature_celsius", getattr(monitor, "current_temperature", None), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_temperature_min_celsius", getattr(monitor, "min_temp", None), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_temperature_max_celsius", getattr(monitor, "max_temp", None), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_enabled", getattr(monitor, "enabled", True), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_selected_drives", _safe_call(monitor.get_drive_count), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_available_drives", _safe_call(monitor.get_available_drive_count), **labels)
        _append_metric(lines, "hako_foundry_drive_monitor_last_updated_timestamp_seconds", _timestamp(getattr(monitor, "last_updated", None)), **labels)


def _collect_powerboard_metrics(lines: List[str], globals_module: Any) -> None:
    powerboards: Dict[int, Any] = getattr(globals_module, "powerboardDict", None) or {}
    _append_metric(lines, "hako_foundry_powerboards", len(powerboards))

    for board_id, powerboard in powerboards.items():
        board_labels = {
            "powerboard": board_id,
            "location": getattr(powerboard, "location", None),
        }
        _append_metric(
            lines,
            "hako_foundry_powerboard_info",
            1,
            hardware_revision=getattr(powerboard, "hardware_revision", None),
            firmware_version=getattr(powerboard, "firmware_version", None),
            **board_labels,
        )
        _append_metric(lines, "hako_foundry_powerboard_connected", getattr(powerboard, "is_connected", None), **board_labels)

        for row, rpm in _iter_indexed(getattr(powerboard, "_current_fan_rpm", None), ("1", "2", "3")):
            _append_metric(lines, "hako_foundry_powerboard_fan_speed_rpm", rpm, row=row, **board_labels)

        pwm_sets = {
            "current": getattr(powerboard, "_current_fan_pwm", None),
            "saved": getattr(powerboard, "_saved_fan_pwm", None),
            "running": getattr(powerboard, "_running_fan_pwm", None),
        }
        for state, pwm_values in pwm_sets.items():
            for row, pwm in _iter_indexed(pwm_values, ("1", "2", "3")):
                _append_metric(lines, "hako_foundry_powerboard_fan_pwm_percent", pwm, row=row, state=state, **board_labels)

        for shunt, watts in _iter_indexed(getattr(powerboard, "_current_wattage", None), ("1", "2", "3", "4")):
            _append_metric(lines, "hako_foundry_powerboard_power_draw_watts", watts, shunt=shunt, **board_labels)

        _append_metric(lines, "hako_foundry_powerboard_power_section_watts", getattr(powerboard, "watt_sec_1_2", None), section="1_2", **board_labels)
        _append_metric(lines, "hako_foundry_powerboard_power_section_watts", getattr(powerboard, "watt_sec_3_4", None), section="3_4", **board_labels)


def _collect_fan_control_metrics(lines: List[str], globals_module: Any) -> None:
    service = getattr(globals_module, "fan_control_service", None)
    if service is None:
        return

    _append_metric(lines, "hako_foundry_fan_control_automatic_enabled", getattr(service, "automatic_control_enabled", False))
    _append_metric(lines, "hako_foundry_fan_control_service_active", getattr(service, "fan_wall_service_active", False))
    _append_metric(lines, "hako_foundry_fan_control_update_interval_seconds", getattr(service, "automatic_update_interval", None))
    _append_metric(lines, "hako_foundry_fan_walls", len(getattr(service, "fan_walls", {})))

    for wall_id, wall in getattr(service, "fan_walls", {}).items():
        labels = {
            "wall": wall_id,
            "name": getattr(wall, "name", None),
            "profile": getattr(wall, "assigned_profile", None),
        }
        _append_metric(lines, "hako_foundry_fan_wall_speed_percent", getattr(wall, "current_speed", None), **labels)
        _append_metric(lines, "hako_foundry_fan_wall_manual", getattr(wall, "manual", True), **labels)


def generate_metrics() -> str:
    import globals

    lines: List[str] = []
    _append_family(lines, "hako_foundry_scrape_timestamp_seconds", "Unix timestamp when Hako Foundry generated the metrics scrape.")
    _append_metric(lines, "hako_foundry_scrape_timestamp_seconds", time.time())

    families = [
        ("hako_foundry_temperature_celsius", "Cached hardware temperature sensor readings in Celsius."),
        ("hako_foundry_drive_temperature_celsius", "Cached SMART drive temperature readings in Celsius."),
        ("hako_foundry_powerboard_fan_speed_rpm", "Cached fan tachometer readings from connected powerboards."),
        ("hako_foundry_powerboard_fan_pwm_percent", "Cached fan PWM percentages from connected powerboards."),
        ("hako_foundry_powerboard_power_draw_watts", "Cached power draw readings from connected powerboards."),
        ("hako_foundry_fan_wall_speed_percent", "Current fan wall speed targets."),
    ]
    for name, help_text in families:
        _append_family(lines, name, help_text)

    _collect_temperature_sensor_metrics(lines, globals)
    _collect_drive_metrics(lines, globals)
    _collect_powerboard_metrics(lines, globals)
    _collect_fan_control_metrics(lines, globals)

    return "\n".join(lines) + "\n"


def register_metrics_endpoint() -> None:
    from fastapi.responses import Response
    from nicegui import app

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_metrics(), media_type=CONTENT_TYPE_LATEST)
