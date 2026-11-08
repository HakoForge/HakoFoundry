"""
Fan Control Service

This module handles all fan control logic that was previously in the overview page.
It provides a clean interface for managing fan speeds across multiple powerboards
with proper semaphore handling and UI feedback. It also includes integrated fan wall
management for profile-based fan control.
"""

import asyncio
import threading
import json
import os
import logging
import time
from typing import Dict, List, Optional, Any, Callable, Tuple, TYPE_CHECKING
from nicegui import app, ui, run

from powerboard import PowerboardError

# Configure logging
logger = logging.getLogger("foundry_logger")

if TYPE_CHECKING:
    from fan_profile_manager import FanControlBackend, FanControlProfile

# Import the interpolate function from fan_control_backend
try:
    from fan_profile_manager import interpolate_fan_speed
except ImportError:
    # Fallback if interpolate_fan_speed is not available
    def interpolate_fan_speed(curve_data, temperature):
        return None

_WALL_NAMES = {
    1: "Fan Wall 1",
    2: "Fan Wall 2",
    3: "Fan Wall 3",
    4: "Auxiliary Fan 1",
    5: "Auxiliary Fan 2",
    6: "Auxiliary Fan 3",
}


class FanOverrideError(Exception):
    """Raised when an override engage/release hardware write fails."""


def _as_pwm_int(value) -> int:
    """Coerce a wall speed to the int 0-100 that Powerboard fan writes require."""
    return max(0, min(100, int(round(float(value)))))


def _default_assignments(legacy_swap: bool) -> Dict[int, Tuple[int, int]]:
    """Return the default (powerboard_location, header_index) for each wall_id."""
    # pb_swap=true: walls 1-3 drive the location-2 board and aux wall 4 the location-1 board; false is the reverse.
    # Walls 5/6 share wall 4's board so every header on that board stays managed (no fan left unmanaged),
    # since legacy installs wire all three headers of that board to aux fans.
    main_loc = 2 if legacy_swap else 1
    aux_loc = 1 if legacy_swap else 2
    return {
        1: (main_loc, 0),
        2: (main_loc, 1),
        3: (main_loc, 2),
        4: (aux_loc, 2),
        5: (aux_loc, 0),
        6: (aux_loc, 1),
    }


class FanWall:
    """Represents a single fan wall that can be controlled by a fan profile."""
    
    def __init__(self, wall_id: int, name: str = None, assigned_profile: Optional[str] = None,
             powerboard_id: Optional[int] = None, header_index: Optional[int] = None):
        self.wall_id = wall_id
        self.name = name
        self.assigned_profile = assigned_profile # Profile name
        self.current_speed = 0  # Current fan speed percentage (0-100)
        self.manual: bool = True  # True for manual control, False for profile control
        self._service_ref = None  # Reference to the service for triggering saves
        self.powerboard_id: Optional[int] = powerboard_id    # which powerboard (1, 2, ...)
        self.header_index: Optional[int] = header_index      # 0=Row1, 1=Row2, 2=Row3
        self.watt_label: Optional[str] = None                # Custom wattage label; None = auto-derive
        self.watt_powerboard_id: Optional[int] = None        # Powerboard for wattage reading (independent of fan control)
        self.watt_attr: Optional[str] = None                 # 'watt_sec_1_2' (J1) or 'watt_sec_3_4' (J2)
    
    def set_service_reference(self, service):
        """Set reference to the fan control service for triggering config saves."""
        self._service_ref = service
    
    def assign_profile(self, profile_name: Optional[str]) -> None:
        """Assign a fan profile to this wall."""
        self.assigned_profile = profile_name
        logger.info(f"{self.name}: Assigned profile '{profile_name}'")
        
        # Trigger immediate config save if service reference is available
        if self._service_ref:
            self._service_ref.save_config_immediate()
    
class FanControlService:
    """Service class to handle fan control operations and fan wall management."""
    
    def __init__(self):
        """Initialize the fan control service."""
        # Semaphores used to allow only one update of fan PWM to be queued per powerboard
        self.update_pwm_semaphore = threading.Semaphore(1)  # For powerboard 1
        self.update_aux_pwm_semaphore = threading.Semaphore(1)  # For powerboard 2
        
        # Flag to prevent callback loops when updating sliders programmatically
        self.updating_sliders_programmatically = False
        
        # Fan wall management
        self.fan_walls: Dict[int, FanWall] = {}
        self.fan_wall_service_active: bool = False
        
        # Automatic fan control
        self.automatic_control_enabled: bool = False
        self.automatic_control_timer: Optional[ui.timer] = None
        self.automatic_update_interval: float = 2.0  # Update every 2 seconds
        
        # Configuration file path
        self.config_file_path = "config/fan_control_config.json"

        # Set by _load_config when the config file exists but cannot be read; shown in Settings.
        self.config_load_error: Optional[str] = None
        # True when an unreadable config could not be moved aside; _save_config then refuses
        # to overwrite it so the only copy of the user's settings is never replaced by defaults.
        self._config_save_blocked: bool = False

        # Pre-override state of every wall targeted by engage_override; None when no override.
        # Structure: {wall_id: {'manual', 'current_speed', 'powerboard_id', 'header_index'}}
        # Keys are only the walls an engage call targeted; release restores those walls alone. The override
        # is in-memory only, so a restart returns every wall to its configured state.
        self.override_snapshot: Optional[Dict[int, Dict]] = None
        # Serialises engage_override/release_override: both await board writes, and an engage
        # rollback interleaved with a release would act on a snapshot the release already cleared.
        self._override_lock = asyncio.Lock()

        # Load configuration on startup
        self._load_config()
        
        # Initialize fan walls based on available powerboards
        self._initialize_fan_walls()
    
    def _load_config(self):
        """Load fan control configuration from file."""
        if not os.path.exists(self.config_file_path):
            logger.info("No fan control config file found, creating default config")
            self.loaded_config = {}
            # Save default configuration for future use
            if self.save_config_immediate():
                logger.info("Default fan control configuration saved to config file")
            else:
                logger.error("Failed to save default fan control configuration")
            return

        try:
            with open(self.config_file_path, 'r') as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError(f"top-level JSON value is {type(loaded).__name__}, expected an object")
        except (OSError, ValueError) as e:
            self._quarantine_unreadable_config(e)
            self.loaded_config = {}
            return

        self.loaded_config = loaded
        # Load automatic control settings
        self.automatic_control_enabled = self.loaded_config.get('automatic_control_enabled', False)
        self.automatic_update_interval = self.loaded_config.get('automatic_update_interval', 2.0)
        self.fan_wall_service_active = self.loaded_config.get('fan_wall_service_active', False)

        logger.info("Fan control configuration loaded successfully")

    def _quarantine_unreadable_config(self, error: Exception) -> None:
        """Move an unreadable config file aside so later default saves cannot overwrite it."""
        backup_path = f"{self.config_file_path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(self.config_file_path, backup_path)
        except OSError as move_error:
            self._config_save_blocked = True
            self.config_load_error = (
                f"Fan control config {self.config_file_path} is unreadable ({error}) and could not be "
                f"moved aside ({move_error}); defaults are in use and settings will not be saved until "
                f"the file is repaired or removed"
            )
        else:
            self.config_load_error = (
                f"Fan control config {self.config_file_path} was unreadable ({error}); it was moved to "
                f"{backup_path} and defaults are in use"
            )
        logger.error(self.config_load_error)

    def _apply_loaded_config(self):
        """Apply loaded configuration to fan walls after they're initialized."""
        loaded = getattr(self, 'loaded_config', None) or {}
        fan_walls_config = loaded.get('fan_walls') or {}

        wall4_entry = fan_walls_config.get('4')
        wall4_is_legacy = isinstance(wall4_entry, dict) and 'powerboard_id' not in wall4_entry

        for wall_id in sorted(self.fan_walls):
            wall = self.fan_walls[wall_id]
            entry = fan_walls_config.get(str(wall_id))
            if not isinstance(entry, dict):
                entry = None

            if entry is None and wall_id in (5, 6) and wall4_is_legacy:
                wall4 = self.fan_walls[4]
                wall.manual = wall4_entry.get('manual', True)
                wall.assigned_profile = wall4_entry.get('assigned_profile', wall.assigned_profile)
                wall.current_speed = wall4.current_speed
                self._needs_explicit_save = True
                logger.info(
                    f"{wall.name}: legacy default assignment PB location {wall.powerboard_id} "
                    f"header {wall.header_index}, inherited from {wall4.name}"
                )
                continue

            # A wall entry without a powerboard_id key is a pre-upgrade config; those walls keep the derived
            # legacy defaults. Stored walls re-seed speed from their own header so the first ping-loop write
            # does not carry the default header's speed onto another header.
            is_stored = entry is not None and 'powerboard_id' in entry
            if is_stored:
                wall.powerboard_id = entry.get('powerboard_id')
                wall.header_index = entry.get('header_index')
                self._seed_current_speed(wall)
            else:
                self._needs_explicit_save = True

            if entry is not None:
                wall.assigned_profile = entry.get('assigned_profile', wall.assigned_profile)
                wall.manual = entry.get('manual', True)
                wall.watt_label = entry.get('watt_label', None)
                wall.watt_powerboard_id = entry.get('watt_powerboard_id', None)
                wall.watt_attr = entry.get('watt_attr', None)

            source = "stored" if is_stored else "legacy default"
            logger.info(
                f"{wall.name}: {source} assignment PB location {wall.powerboard_id} header {wall.header_index}, "
                f"Profile={wall.assigned_profile}, Manual={wall.manual}"
            )

        logger.info("Fan wall configuration applied successfully")

    def _validate_assignments(self) -> int:
        """Unassign invalid or duplicate (powerboard_id, header_index) pairs. Returns the correction count."""
        corrections = 0
        claimed: Dict[Tuple[int, int], int] = {}
        for wall_id in sorted(self.fan_walls):
            wall = self.fan_walls[wall_id]
            pb_id, header = wall.powerboard_id, wall.header_index
            if pb_id is None and header is None:
                continue

            pb_valid = isinstance(pb_id, int) and not isinstance(pb_id, bool) and pb_id in (1, 2)
            header_valid = isinstance(header, int) and not isinstance(header, bool) and header in (0, 1, 2)
            if not (pb_valid and header_valid):
                logger.warning(
                    f"{wall.name}: invalid assignment powerboard_id={pb_id!r} header_index={header!r}; "
                    f"set to unassigned"
                )
                wall.powerboard_id = None
                wall.header_index = None
                corrections += 1
                continue

            # Lowest wall_id keeps a contested header so the winner is deterministic across restarts; the rest become Unassigned in Settings.
            owner = claimed.get((pb_id, header))
            if owner is not None:
                logger.warning(
                    f"{wall.name}: PB location {pb_id} header {header} already used by "
                    f"{self.fan_walls[owner].name}; set to unassigned"
                )
                wall.powerboard_id = None
                wall.header_index = None
                corrections += 1
                continue
            claimed[(pb_id, header)] = wall_id
        return corrections

    def _save_config(self) -> bool:
        """Save fan control configuration to file atomically. Returns True on success."""
        if self._config_save_blocked:
            logger.error(f"Not saving fan control config: {self.config_load_error}")
            return False
        # Write to a sibling temp file then os.replace so a crash never leaves a truncated config.
        tmp_path = self.config_file_path + ".tmp"
        try:
            logger.debug(f"Attempting to save config to: {self.config_file_path}")
            
            # Ensure config directory exists
            config_dir = os.path.dirname(self.config_file_path)
            if not os.path.exists(config_dir):
                os.makedirs(config_dir)
                logger.info(f"Created config directory: {config_dir}")
            
            # Prepare configuration data
            config = {
                'automatic_control_enabled': self.automatic_control_enabled,
                'automatic_update_interval': self.automatic_update_interval,
                'fan_wall_service_active': self.fan_wall_service_active,
                'fan_walls': {}
            }
            
            # Save fan wall configurations
            for wall_id, wall in self.fan_walls.items():
                persisted = {'manual': wall.manual, 'current_speed': wall.current_speed}
                # save_config_immediate is called from many UI paths; the override must never reach disk, so
                # overridden walls persist their snapshot values instead of live values.
                if self.is_wall_overridden(wall_id):
                    persisted = self.override_snapshot[wall_id]
                config['fan_walls'][str(wall_id)] = {
                    'name': wall.name,
                    'assigned_profile': wall.assigned_profile,
                    'manual': persisted['manual'],
                    'current_speed': persisted['current_speed'],
                    'powerboard_id': wall.powerboard_id,
                    'header_index': wall.header_index,
                    'watt_label': wall.watt_label,
                    'watt_powerboard_id': wall.watt_powerboard_id,
                    'watt_attr': wall.watt_attr,
                }
            
            logger.debug(f"Config data prepared: {len(config['fan_walls'])} fan walls")
            
            # Write to a temp file in the same directory, then os.replace, so a failed write
            # leaves the previous config intact instead of a truncated file.
            with open(tmp_path, 'w') as f:
                json.dump(config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_file_path)

            logger.info("Fan control configuration saved successfully")
            return True

        except Exception as e:
            logger.error(f"Error saving fan control config to {self.config_file_path}: {e}", exc_info=True)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError as cleanup_error:
                logger.error(f"Could not remove temporary fan control config {tmp_path}: {cleanup_error}")
            return False
    
    def save_config_delayed(self, delay: float = 0.5):
        """Save configuration with a delay to batch multiple rapid changes."""
        # Cancel any existing timer
        if hasattr(self, '_save_timer') and self._save_timer:
            try:
                self._save_timer.cancel()
            except:
                pass  # Timer might already be finished
        
        # Create new timer to save after delay
        def save_and_clear():
            self._save_config()
            self._save_timer = None
            
        self._save_timer = ui.timer(delay, save_and_clear, once=True)
    
    def save_config_immediate(self) -> bool:
        """Save configuration immediately without delay. Returns True on success."""
        # Cancel any pending delayed save
        if hasattr(self, '_save_timer') and self._save_timer:
            try:
                self._save_timer.cancel()
            except:
                pass
            self._save_timer = None
        
        # Save immediately
        return self._save_config()
    
    def test_save_config(self):
        """Test method to manually trigger config save for debugging."""
        logger.debug("Manual save test triggered")
        self.save_config_immediate()
        return True
        
    def fan_speed_current(self, pb) -> bool:
        """Check if hardware fan speeds match current wall speeds for a given powerboard."""
        import globals
        if pb not in globals.powerboardDict:
            return True
        pb_obj = globals.powerboardDict[pb]
        running = pb_obj.get_running_fan_pwm()
        for wall in self.fan_walls.values():
            if wall.powerboard_id == pb_obj.location and wall.header_index is not None:
                if _as_pwm_int(wall.current_speed) != running[wall.header_index]:
                    return False
        return True
    def update_powerboard_fan_speed(self, pb) -> None:
        """Update hardware fan speed for a given powerboard using current wall assignments."""
        import globals
        if pb not in globals.powerboardDict:
            return
        pb_obj = globals.powerboardDict[pb]
        speeds = list(pb_obj.get_running_fan_pwm())
        for wall in self.fan_walls.values():
            if wall.powerboard_id == pb_obj.location and wall.header_index is not None:
                speeds[wall.header_index] = _as_pwm_int(wall.current_speed)
        pb_obj.update_fan_speed(*speeds)

    def is_override_active(self) -> bool:
        """Return True while an API fan override is engaged."""
        return self.override_snapshot is not None

    def is_wall_overridden(self, wall_id: int) -> bool:
        """Return True when wall_id is part of the active override."""
        return self.override_snapshot is not None and wall_id in self.override_snapshot

    async def _write_board_arrays(self, pb_arrays: Dict[int, list]) -> Dict[int, tuple]:
        """Write per-board running arrays in location order; return pre-write arrays of boards written.

        Raises FanOverrideError on the first failed write, after attempting to restore every
        board already written to its pre-write array.
        """
        written: Dict[int, tuple] = {}
        for location in sorted(pb_arrays):
            pb_obj = self._pb_for_location(location)
            # Powerboard.update_fan_speed updates its running cache only after a successful serial write, so after
            # a failed rollback the ping loop sees fan_speed_current() False and re-applies wall speeds.
            pre_write = pb_obj.get_running_fan_pwm()
            try:
                await run.io_bound(pb_obj.update_fan_speed, *pb_arrays[location])
            except Exception as e:
                for done_location, previous in written.items():
                    try:
                        await run.io_bound(self._pb_for_location(done_location).update_fan_speed, *previous)
                    except Exception as rollback_error:
                        logger.error(f"Override rollback of powerboard {done_location} failed: {rollback_error}")
                raise FanOverrideError(f"powerboard {location} write failed: {e}") from e
            written[location] = pre_write
        return written

    def _board_arrays_for(self, assignments: Dict[int, tuple]) -> Dict[int, list]:
        """Build running arrays for connected boards from {wall_id: (location, header, speed)}."""
        pb_arrays: Dict[int, list] = {}
        for location, header_index, speed in assignments.values():
            pb_obj = self._pb_for_location(location)
            if pb_obj is None or header_index is None:
                continue
            if location not in pb_arrays:
                pb_arrays[location] = list(pb_obj.get_running_fan_pwm())
            pb_arrays[location][header_index] = _as_pwm_int(speed)
        return pb_arrays

    async def engage_override(self, targets: Dict[int, int]) -> Dict[str, Any]:
        """Force the targeted walls to manual mode at the given speeds and write them to hardware.

        Raises FanOverrideError when a board write fails; boards already written, wall
        fields and the snapshot are rolled back first.
        """
        async with self._override_lock:
            return await self._engage_override_locked(targets)

    async def _engage_override_locked(self, targets: Dict[int, int]) -> Dict[str, Any]:
        """engage_override body; caller holds _override_lock."""
        snapshot_taken = self.override_snapshot is None
        if self.override_snapshot is None:
            self.override_snapshot = {}

        applied: Dict[str, int] = {}
        skipped: list = []
        newly_added: list = []
        touched: Dict[int, tuple] = {}
        hardware: Dict[int, tuple] = {}

        for wall_id, speed in targets.items():
            wall = self.fan_walls.get(wall_id)
            if wall is None:
                continue
            if wall.powerboard_id is None or wall.header_index is None:
                skipped.append({"wall": wall.name, "reason": "no hardware assignment"})
                continue
            if wall_id not in self.override_snapshot:
                self.override_snapshot[wall_id] = {
                    'manual': wall.manual,
                    'current_speed': wall.current_speed,
                    'powerboard_id': wall.powerboard_id,
                    'header_index': wall.header_index,
                }
                newly_added.append(wall_id)
            touched[wall_id] = (wall.manual, wall.current_speed)
            wall.manual = True
            wall.current_speed = speed
            if self._pb_for_location(wall.powerboard_id) is None:
                skipped.append({"wall": wall.name, "reason": "powerboard not connected"})
                continue
            applied[wall.name] = speed
            hardware[wall_id] = (wall.powerboard_id, wall.header_index, speed)

        try:
            await self._write_board_arrays(self._board_arrays_for(hardware))
        except FanOverrideError:
            for wall_id, (manual, current_speed) in touched.items():
                self.fan_walls[wall_id].manual = manual
                self.fan_walls[wall_id].current_speed = current_speed
            if self.override_snapshot is not None:
                for wall_id in newly_added:
                    self.override_snapshot.pop(wall_id, None)
            raise
        finally:
            if not self.override_snapshot:
                self.override_snapshot = None

        snapshot_taken = snapshot_taken and self.override_snapshot is not None
        return {"applied": applied, "skipped": skipped, "snapshot_taken": snapshot_taken}

    async def release_override(self) -> bool:
        """Restore every overridden wall and write the restored speeds to hardware.

        Returns False when no override is active. Raises FanOverrideError when a board write
        fails; the snapshot is kept so the release can be retried.
        """
        async with self._override_lock:
            return await self._release_override_locked()

    async def _release_override_locked(self) -> bool:
        """release_override body; caller holds _override_lock."""
        if self.override_snapshot is None:
            return False

        restored: Dict[int, tuple] = {}
        for wall_id, saved in self.override_snapshot.items():
            wall = self.fan_walls.get(wall_id)
            if wall is None:
                continue
            wall.manual = saved['manual']
            wall.current_speed = saved['current_speed']
            restored[wall_id] = (saved['powerboard_id'], saved['header_index'], saved['current_speed'])

        pb_arrays = self._board_arrays_for(restored)
        first_error: Optional[Exception] = None
        for location in sorted(pb_arrays):
            try:
                await run.io_bound(self._pb_for_location(location).update_fan_speed, *pb_arrays[location])
            except Exception as e:
                logger.error(f"Override release write to powerboard {location} failed: {e}")
                if first_error is None:
                    first_error = FanOverrideError(f"powerboard {location} write failed: {e}")
        if first_error is not None:
            raise first_error

        self.override_snapshot = None
        self.save_config_immediate()
        return True

    def _pb_for_location(self, location: Optional[int]):
        """Return the Powerboard whose V: location matches, or None."""
        if location is None:
            return None
        import globals
        for pb in globals.powerboardDict.values():
            if pb.location == location:
                return pb
        return None

    def _seed_current_speed(self, wall: FanWall) -> None:
        """Seed wall.current_speed from its assigned board's running PWM when that board is connected."""
        if wall.header_index not in (0, 1, 2):
            return
        pb_obj = self._pb_for_location(wall.powerboard_id)
        if pb_obj is not None:
            wall.current_speed = pb_obj.get_running_fan_pwm()[wall.header_index]

    def _initialize_fan_walls(self):
        """Create all six fan walls, apply stored config, and persist migrated assignments."""
        import globals  # Import here to avoid circular import
        fan_profiles = self.get_fan_profile_options()
        default_profile = fan_profiles[0] if fan_profiles else None

        legacy_swap = globals.layoutState.get_legacy_pb_swap() if globals.layoutState else False
        for wall_id, (pb_location, header_index) in _default_assignments(legacy_swap).items():
            wall = FanWall(wall_id, _WALL_NAMES[wall_id], default_profile,
                           powerboard_id=pb_location, header_index=header_index)
            wall.set_service_reference(self)
            self._seed_current_speed(wall)
            self.fan_walls[wall_id] = wall
            logger.info(f"Initialized {wall.name} (default PB location {pb_location} header {header_index})")

        # Migration is idempotent: pb_swap stays in layout_config.json, so a failed save here is retried
        # on the next start with the same derived mapping. Explicit stored assignments win over pb_swap.
        self._needs_explicit_save = legacy_swap
        self._apply_loaded_config()
        self._validate_assignments()

        if self._needs_explicit_save:
            if self.save_config_immediate():
                logger.info(f"fan wall assignments persisted explicitly (legacy_pb_swap={legacy_swap})")
            else:
                logger.error(
                    "legacy fan wall assignments could not be persisted; pb_swap is retained in "
                    "layout_config.json and migration will be retried on next start"
                )

        # Timer registers when any board exists; a PB2-only host must still be polled and driven.
        if globals.powerboardDict:
            self._start_ping_timer()

    def _start_ping_timer(self) -> None:
        """Register the 3 s powerboard control loop."""
        app.timer(3.0, self.ping_powerboards)

    async def ping_powerboards(self) -> None:
        """Poll every connected powerboard and push wall speeds that differ from hardware."""
        import globals

        # Each board is isolated so one PowerboardError cannot stop the other board; serial I/O runs off
        # the event loop because it blocks the UI.
        for pb_key in sorted(globals.powerboardDict):
            pb_obj = globals.powerboardDict[pb_key]
            try:
                await run.io_bound(pb_obj.update_powerboard_state)
                if not self.fan_speed_current(pb_key):
                    await run.io_bound(self.update_powerboard_fan_speed, pb_key)
            except Exception as e:
                logger.error(f"ping_powerboards: board {pb_obj.location} failed: {e}")

        # Only profile-driven walls are recomputed; overridden walls are manual=True so the loop leaves them alone.
        for fan_wall in self.fan_walls.values():
            if not fan_wall.manual:
                fan_wall.current_speed = self._update_single_fan_wall(fan_wall.wall_id)

    def assign_profile_to_wall(self, wall_id: int, profile_name: Optional[str]) -> bool:
        """Assign a fan profile to a specific wall."""
        if wall_id not in self.fan_walls:
            logger.warning(f"Wall {wall_id} does not exist")
            return False
        
        # Validate profile exists if provided
        if profile_name:
            import globals
            if globals.fan_profile_service and not globals.fan_profile_service.get_profile_by_name(profile_name):
                logger.warning(f"Profile '{profile_name}' does not exist")
                return False
        
        self.fan_walls[wall_id].assign_profile(profile_name)
        
        # Save configuration immediately when profile assignment changes
        self.save_config_immediate()
        
        return True
    
    def set_wall_assignment(self, wall_id: int, powerboard_id: Optional[int], header_index: Optional[int]) -> bool:
        """Assign a fan wall to a powerboard (by location) and header. Enforces uniqueness."""
        if wall_id not in self.fan_walls:
            return False

        # Uniqueness check: no other wall may share the same (location, header)
        if powerboard_id is not None and header_index is not None:
            for wid, wall in self.fan_walls.items():
                if wid == wall_id:
                    continue
                if wall.powerboard_id == powerboard_id and wall.header_index == header_index:
                    logger.warning(
                        f"Assignment conflict: Wall {wid} already uses PB location {powerboard_id} Row {header_index + 1}"
                    )
                    return False

        self.fan_walls[wall_id].powerboard_id = powerboard_id
        self.fan_walls[wall_id].header_index = header_index
        self.save_config_immediate()
        logger.info(f"Wall {wall_id} assigned to PB location {powerboard_id} Row {header_index + 1 if header_index is not None else '-'}")
        return True

    def set_wall_wattage_source(self, wall_id: int, powerboard_id: Optional[int], watt_attr: Optional[str]) -> None:
        """Set the independent wattage source (powerboard + connector) for a wall's display row."""
        if wall_id not in self.fan_walls:
            return
        self.fan_walls[wall_id].watt_powerboard_id = powerboard_id
        self.fan_walls[wall_id].watt_attr = watt_attr
        self.save_config_immediate()
        logger.info(f"Wall {wall_id} wattage source set to PB{powerboard_id}:{watt_attr}")

    def set_manual_mode(self, wall_id: int, manual: bool = True) -> bool:
        """Set manual mode for a specific fan wall."""
        if wall_id not in self.fan_walls:
            return False
        
        self.fan_walls[wall_id].manual = manual
        mode = "manual" if manual else "profile"
        logger.info(f"{self.fan_walls[wall_id].name} set to {mode} mode")

        # Save configuration immediately when manual mode changes
        self.save_config_immediate()

        return True
    
    def _update_single_fan_wall(self, wall_id: int) -> Optional[float]:
        """Update a single fan wall based on its assigned profile."""
        wall = self.fan_walls.get(wall_id)
        if not wall:
            return None
        
        # If in manual mode, don't update based on profile
        if wall.manual:
            return wall.current_speed
        
        if not wall.assigned_profile:
            # No profile assigned - set to safe default speed
            wall.current_speed = 50
            return 50
        
        import globals
        if not globals.fan_profile_service:
            logger.warning(f"Warning: {wall.name}: Fan backend not available")
            wall.current_speed = 50
            return 50
            
        profile = globals.fan_profile_service.get_profile_by_name(wall.assigned_profile)
        if not profile:
            logger.warning(f"Warning: {wall.name}: Assigned profile '{wall.assigned_profile}' not found")
            
            # Try to assign the next available profile
            available_profiles = self.get_fan_profile_options()
            if available_profiles:
                new_profile = available_profiles[0]  # Get first available profile
                logger.info(f"{wall.name}: Auto-assigning profile '{new_profile}'")
                wall.assign_profile(new_profile)
                
                # Try to get the new profile and update again
                profile = globals.fan_profile_service.get_profile_by_name(new_profile)
                if profile:
                    # Calculate and apply speed from the new profile
                    max_speed = round(self._calculate_max_speed_from_profile(profile))
                    wall.current_speed = max_speed
                    logger.info(f"{wall.name}: Updated speed to {max_speed}% from auto-assigned profile '{new_profile}'")
                    return max_speed
            
            # If no profiles available or assignment failed, use safe default
            wall.current_speed = 50
            return 50
        
        # Calculate maximum speed from all curves in the profile
        max_speed = round(self._calculate_max_speed_from_profile(profile))
        
        # Apply the calculated speed
        wall.current_speed = max_speed
        logger.info(f"{wall.name}: Updated speed to {max_speed}% from profile '{wall.assigned_profile}'")
        
        return max_speed
    
    def _calculate_max_speed_from_profile(self, profile: 'FanControlProfile') -> float:
        """Calculate the maximum fan speed from all curves in a profile."""
        import globals
        
        all_curves = profile.get_all_curves()
        if not all_curves:
            return 50.0  # Safe default
        
        max_speed = 0.0
        curves_with_sensors = 0
        
        for curve_name, curve in all_curves.items():
            if not curve.sensor:
                continue  # Skip curves without sensors
            
            # Get current temperature for this curve's sensor
            current_temp = globals.fan_profile_service.get_sensor_temperature(curve.sensor)
            if current_temp is None:
                continue  # Skip if temperature unavailable
            
            # Calculate fan speed for this curve
            curve_speed = interpolate_fan_speed(curve._data, current_temp)
            if curve_speed is not None:
                max_speed = max(max_speed, curve_speed)
                curves_with_sensors += 1
        
        # If no curves had valid sensors/temperatures, use safe default
        if curves_with_sensors == 0:
            return 50.0
        
        return max_speed
    
    def set_automatic_control_enabled(self, enabled: bool):
        """Enable or disable automatic fan control."""
        if self.automatic_control_enabled != enabled:
            self.automatic_control_enabled = enabled
            logger.info(f"Automatic fan control {'enabled' if enabled else 'disabled'}")
            
            # Save configuration immediately when automatic control setting changes
            self.save_config_immediate()
    
    def set_fan_wall_service_active(self, active: bool):
        """Enable or disable the fan wall service."""
        if self.fan_wall_service_active != active:
            self.fan_wall_service_active = active
            logger.info(f"Fan wall service {'activated' if active else 'deactivated'}")
            
            # Save configuration immediately when service state changes
            self.save_config_immediate()
    
    def set_automatic_update_interval(self, interval: float):
        """Set the automatic update interval."""
        if self.automatic_update_interval != interval:
            self.automatic_update_interval = interval
            logger.info(f"Automatic update interval set to {interval} seconds")
            
            # Save configuration immediately when interval changes
            self.save_config_immediate()
    
    async def _perform_automatic_update(self):
        """Perform automatic fan speed update based on fan profiles."""
        if not self.automatic_control_enabled or not self.fan_wall_service_active:
            return

        try:
            import globals
            pb_speeds: Dict[int, list] = {}
            changed_walls = []

            for wall_id in [1, 2, 3]:
                wall = self.fan_walls.get(wall_id)
                if not wall or wall.manual or not wall.assigned_profile:
                    continue
                if wall.powerboard_id is None:
                    continue

                speed = self._update_single_fan_wall(wall_id)
                if speed is not None:
                    loc = wall.powerboard_id
                    pb_obj = self._pb_for_location(loc)
                    if pb_obj is None:
                        continue
                    if loc not in pb_speeds:
                        pb_speeds[loc] = {'pb': pb_obj, 'speeds': list(pb_obj.get_running_fan_pwm())}
                    pb_speeds[loc]['speeds'][wall.header_index] = round(speed)
                    changed_walls.append(f"Wall {wall_id}")

            for entry in pb_speeds.values():
                pb_obj = entry['pb']
                speeds = entry['speeds']
                pb_obj.set_running_fan_pwm(*speeds)
                await run.io_bound(pb_obj.update_fan_speed, *speeds)

            if changed_walls:
                ui.notify(
                    f"🤖 Auto: {', '.join(changed_walls)} updated",
                    position='bottom-right', type='info', group=False, timeout=1000
                )

        except Exception as e:
            logger.error(f"Error in automatic fan control update: {e}")
    
    def get_fan_wall_status(self, wall_id: int) -> Optional[Dict[str, Any]]:
        """Get status of a specific fan wall."""
        wall = self.fan_walls.get(wall_id)
        return wall.get_status() if wall else None
    
    def get_all_fan_walls_status(self) -> Dict[int, Dict[str, Any]]:
        """Get status of all fan walls."""
        return {wall_id: wall.get_status() for wall_id, wall in self.fan_walls.items()}
        
    def set_slider_value_without_callback(self, slider, value: float):
        """Set slider value without triggering the change callback."""
        if slider:
            # Set flag to prevent callback execution
            self.updating_sliders_programmatically = True
            # Set the value
            slider.set_value(value)
            # Reset flag after a short delay to allow any pending events to clear
            ui.timer(0.05, lambda: setattr(self, 'updating_sliders_programmatically', False), once=True)
    
    def get_current_slider_values(self, slider_list: List) -> tuple:
        """Get current slider values safely."""
        return (
            slider_list[0].value if slider_list[0] else 0,
            slider_list[1].value if slider_list[1] else 0,
            slider_list[2].value if slider_list[2] else 0
        )
    
    def get_auxiliary_slider_value(self, slider_list: List) -> float:
        """Get auxiliary slider value safely."""
        return slider_list[3].value if len(slider_list) > 3 and slider_list[3] else 0
    
    async def request_update_fan_speed(self, slider_list: List):
        """Request fan speed update for walls 1–3 using their current PB/header assignments."""
        if self.updating_sliders_programmatically:
            return

        acquired = self.update_pwm_semaphore.acquire(blocking=False)
        if acquired:
            try:
                import globals
                # Build per-PB speed maps from walls 1/2/3
                pb_speeds: Dict[int, list] = {}
                for wall_id, slider_idx in ((1, 0), (2, 1), (3, 2)):
                    wall = self.fan_walls.get(wall_id)
                    if not wall or wall.powerboard_id is None or wall.header_index is None:
                        continue
                    if slider_idx >= len(slider_list) or slider_list[slider_idx] is None:
                        continue
                    loc = wall.powerboard_id
                    pb_obj = self._pb_for_location(loc)
                    if pb_obj is None:
                        continue
                    if loc not in pb_speeds:
                        pb_speeds[loc] = {'pb': pb_obj, 'speeds': list(pb_obj.get_running_fan_pwm())}
                    pb_speeds[loc]['speeds'][wall.header_index] = _as_pwm_int(slider_list[slider_idx].value)

                for loc, entry in pb_speeds.items():
                    pb_obj = entry['pb']
                    speeds = entry['speeds']
                    await run.io_bound(pb_obj.semaphore.acquire)
                    pb_obj.semaphore.release()
                    # update_fan_speed refreshes the running cache only on success, so a failed write stays visible to the ping loop.
                    await run.io_bound(pb_obj.update_fan_speed, *speeds)
                    ui.notify(
                        f"PWM updated {speeds[0]}, {speeds[1]}, {speeds[2]}",
                        position='bottom-right', type='positive', group=False
                    )
            except Exception as e:
                logger.error(f"Error updating fan speed: {e}")
                ui.notify("Fan speed update failed", position='bottom-right', type='negative', group=False)
            finally:
                self.update_pwm_semaphore.release()

    async def request_update_auxiliary_fan_speed(self, slider_list: List):
        """Request auxiliary fan speed update for wall 4 using its current PB/header assignment."""
        if self.updating_sliders_programmatically:
            return

        import globals
        wall = self.fan_walls.get(4)
        if not wall or wall.powerboard_id is None or wall.header_index is None:
            return
        pb_obj = self._pb_for_location(wall.powerboard_id)
        if pb_obj is None:
            return

        acquired = self.update_aux_pwm_semaphore.acquire(blocking=False)
        if acquired:
            try:
                await run.io_bound(pb_obj.semaphore.acquire)
                pb_obj.semaphore.release()

                aux_pwm = _as_pwm_int(self.get_auxiliary_slider_value(slider_list))
                speeds = list(pb_obj.get_running_fan_pwm())
                speeds[wall.header_index] = aux_pwm

                await run.io_bound(pb_obj.update_fan_speed, *speeds)
                ui.notify(
                    f"Auxiliary PWM updated {aux_pwm}",
                    position='bottom-right', type='positive', group=False
                )
            except Exception as e:
                logger.error(f"Error updating auxiliary fan speed: {e}")
                ui.notify("Auxiliary fan speed update failed", position='bottom-right', type='negative', group=False)
            finally:
                self.update_aux_pwm_semaphore.release()

    # Same wall-assignment traversal as request_update_fan_speed so Set writes saved+running values to the assigned board and header.
    async def set_fan_speed(self, slider_list: List):
        """Write every assigned wall's slider value to its powerboard's EEPROM."""
        wall_slider_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
        pb_speeds: Dict[int, dict] = {}
        for wall_id, slider_idx in wall_slider_map.items():
            wall = self.fan_walls.get(wall_id)
            if not wall or wall.powerboard_id is None or wall.header_index is None:
                continue
            if slider_idx >= len(slider_list) or slider_list[slider_idx] is None:
                continue
            pb_obj = self._pb_for_location(wall.powerboard_id)
            if pb_obj is None:
                continue
            loc = wall.powerboard_id
            if loc not in pb_speeds:
                pb_speeds[loc] = {'pb': pb_obj, 'speeds': list(pb_obj.get_saved_fan_pwm()), 'walls': []}
            pb_speeds[loc]['speeds'][wall.header_index] = _as_pwm_int(slider_list[slider_idx].value)
            pb_speeds[loc]['walls'].append(wall)

        failed = False
        for loc, entry in pb_speeds.items():
            pb_obj = entry['pb']
            speeds = entry['speeds']
            try:
                await run.io_bound(pb_obj.set_fan_speed, *speeds)
                pb_obj.set_running_fan_pwm(*speeds)
                for wall in entry['walls']:
                    wall.current_speed = speeds[wall.header_index]
            except (PowerboardError, ValueError) as e:
                failed = True
                logger.error(f"Saving fan speed to powerboard {loc} failed: {e}")
                ui.notify(f"Saving PWM to powerboard {loc} failed: {e}",
                          position='bottom-right', type='negative', group=False)

        self.save_config_immediate()
        if not failed:
            ui.notify("PWM set.", position='bottom-right', type='positive', group=False)

    async def dialog_handler_discard(self, slider_list: List,
                                   update_fan_speed_callback: Callable,
                                   update_aux_fan_speed_callback: Callable):
        """Handle discarding fan speed changes, restoring saved values from each wall's assigned PB."""
        import globals

        # Reset main fan walls 1/2/3
        for wall_id, slider_idx in ((1, 0), (2, 1), (3, 2)):
            wall = self.fan_walls.get(wall_id)
            if not wall or wall.powerboard_id is None or wall.header_index is None:
                continue
            pb_obj = self._pb_for_location(wall.powerboard_id)
            if pb_obj is None:
                continue
            if slider_idx >= len(slider_list) or slider_list[slider_idx] is None:
                continue
            saved = pb_obj.get_saved_fan_pwm()
            self.set_slider_value_without_callback(slider_list[slider_idx], saved[wall.header_index])

        await update_fan_speed_callback()

        # Reset aux wall 4
        wall4 = self.fan_walls.get(4)
        pb4 = self._pb_for_location(wall4.powerboard_id) if (wall4 and wall4.powerboard_id is not None) else None
        if (pb4 and wall4.header_index is not None
                and len(slider_list) > 3 and slider_list[3] is not None):
            saved4 = pb4.get_saved_fan_pwm()
            self.set_slider_value_without_callback(slider_list[3], saved4[wall4.header_index])
            await update_aux_fan_speed_callback()

        # Restore running PWM on each PB to its saved values
        for pb_id, pb in globals.powerboardDict.items():
            saved = pb.get_saved_fan_pwm()
            pb.set_running_fan_pwm(*saved)

        ui.notify(
            "Fan speeds reset to saved values",
            position='bottom-right', type='positive', group=False
        )

    def check_for_changes(self, slider_list: List) -> bool:
        """Check if current slider values differ from saved powerboard values."""
        import globals

        for wall_id, slider_idx in ((1, 0), (2, 1), (3, 2)):
            wall = self.fan_walls.get(wall_id)
            if not wall or wall.powerboard_id is None or wall.header_index is None:
                continue
            pb_obj = self._pb_for_location(wall.powerboard_id)
            if pb_obj is None:
                continue
            if slider_idx >= len(slider_list) or slider_list[slider_idx] is None:
                continue
            saved = pb_obj.get_saved_fan_pwm()
            if slider_list[slider_idx].value != saved[wall.header_index]:
                return True

        wall4 = self.fan_walls.get(4)
        pb4 = self._pb_for_location(wall4.powerboard_id) if (wall4 and wall4.powerboard_id is not None) else None
        if (pb4 and wall4.header_index is not None
                and len(slider_list) > 3 and slider_list[3] is not None):
            saved4 = pb4.get_saved_fan_pwm()
            if slider_list[3].value != saved4[wall4.header_index]:
                return True

        return False

    def get_saved_pwm_values(self) -> tuple:
        """Get saved PWM values from each wall's assigned powerboard/header."""
        import globals

        wall_saved = [0, 0, 0]
        for wall_id in [1, 2, 3]:
            wall = self.fan_walls.get(wall_id)
            pb_obj = self._pb_for_location(wall.powerboard_id) if wall else None
            if pb_obj and wall.header_index is not None:
                wall_saved[wall_id - 1] = pb_obj.get_saved_fan_pwm()[wall.header_index]

        aux_saved = 0
        wall4 = self.fan_walls.get(4)
        pb4 = self._pb_for_location(wall4.powerboard_id) if wall4 else None
        if pb4 and wall4.header_index is not None:
            aux_saved = pb4.get_saved_fan_pwm()[wall4.header_index]

        return tuple(wall_saved), aux_saved

    def get_fan_profile_options(self) -> List[str]:
        """Get available fan profile options."""
        import globals
        
        profile_options = []
        if globals.fan_profile_service:
            profile_options = globals.fan_profile_service.get_profile_names()
        return profile_options