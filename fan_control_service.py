"""
Fan Control Service

This module handles all fan control logic that was previously in the overview page.
It provides a clean interface for managing fan speeds across multiple powerboards
with proper semaphore handling and UI feedback. It also includes integrated fan wall
management for profile-based fan control.
"""

import threading
import json
import os
import logging
from typing import Dict, List, Optional, Any, Callable, TYPE_CHECKING
from nicegui import app, ui, run

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
        
        # Load configuration on startup
        self._load_config()
        
        # Initialize fan walls based on available powerboards
        self._initialize_fan_walls()
    
    def _load_config(self):
        """Load fan control configuration from file."""
        try:
            if os.path.exists(self.config_file_path):
                with open(self.config_file_path, 'r') as f:
                    self.loaded_config = json.load(f)
                
                # Load automatic control settings
                self.automatic_control_enabled = self.loaded_config.get('automatic_control_enabled', False)
                self.automatic_update_interval = self.loaded_config.get('automatic_update_interval', 2.0)
                self.fan_wall_service_active = self.loaded_config.get('fan_wall_service_active', False)
                
                logger.info("Fan control configuration loaded successfully")
            else:
                logger.info("No fan control config file found, creating default config")
                self.loaded_config = {}
                # Save default configuration for future use
                try:
                    self.save_config_immediate()
                    logger.info("Default fan control configuration saved to config file")
                except Exception as e:
                    logger.error(f"Failed to save default fan control configuration: {e}")
                
        except Exception as e:
            logger.error(f"Error loading fan control config: {e}")
            logger.info("Using default configuration")
            self.loaded_config = {}
    
    def _apply_loaded_config(self):
        """Apply loaded configuration to fan walls after they're initialized."""
        if not hasattr(self, 'loaded_config') or not self.loaded_config:
            return
        
        # Apply fan wall configurations
        fan_walls_config = self.loaded_config.get('fan_walls', {})
        for wall_id_str, wall_config in fan_walls_config.items():
            wall_id = int(wall_id_str)
            if wall_id in self.fan_walls:
                wall = self.fan_walls[wall_id]
                wall.assigned_profile = wall_config.get('assigned_profile', wall.assigned_profile)
                wall.manual = wall_config.get('manual', True)
                wall.powerboard_id = wall_config.get('powerboard_id', wall.powerboard_id)
                wall.header_index = wall_config.get('header_index', wall.header_index)
                wall.watt_label = wall_config.get('watt_label', None)
                wall.watt_powerboard_id = wall_config.get('watt_powerboard_id', None)
                wall.watt_attr = wall_config.get('watt_attr', None)
                # Don't override current_speed from powerboard readings
                logger.info(f"Applied config to {wall.name}: Profile={wall.assigned_profile}, Manual={wall.manual}")
        
        logger.info("Fan wall configuration applied successfully")
    
    def _save_config(self):
        """Save fan control configuration to file."""
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
                config['fan_walls'][str(wall_id)] = {
                    'name': wall.name,
                    'assigned_profile': wall.assigned_profile,
                    'manual': wall.manual,
                    'current_speed': wall.current_speed,
                    'powerboard_id': wall.powerboard_id,
                    'header_index': wall.header_index,
                    'watt_label': wall.watt_label,
                    'watt_powerboard_id': wall.watt_powerboard_id,
                    'watt_attr': wall.watt_attr,
                }
            
            logger.debug(f"Config data prepared: {len(config['fan_walls'])} fan walls")
            
            # Write to file
            with open(self.config_file_path, 'w') as f:
                json.dump(config, f, indent=4)
            
            logger.info("Fan control configuration saved successfully")
            
        except Exception as e:
            logger.error(f"Error saving fan control config: {e}")
            import traceback
            traceback.print_exc()
    
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
    
    def save_config_immediate(self):
        """Save configuration immediately without delay."""
        # Cancel any pending delayed save
        if hasattr(self, '_save_timer') and self._save_timer:
            try:
                self._save_timer.cancel()
            except:
                pass
            self._save_timer = None
        
        # Save immediately
        self._save_config()
    
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
                if wall.current_speed != running[wall.header_index]:
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
                speeds[wall.header_index] = wall.current_speed
        pb_obj.update_fan_speed(*speeds)

    def _pb_for_location(self, location: Optional[int]):
        """Return the Powerboard whose V: location matches, or None."""
        if location is None:
            return None
        import globals
        for pb in globals.powerboardDict.values():
            if pb.location == location:
                return pb
        return None

    def _initialize_fan_walls(self):
        """Initialize fan walls based on powerboard availability."""
        import globals  # Import here to avoid circular import
        fan_profiles = self.get_fan_profile_options()
        default_profile = fan_profiles[0] if fan_profiles else None
        
        # Initialize main fan walls if powerboard 1 is available
        if 1 in globals.powerboardDict:
            pb1 = globals.powerboardDict[1]
            pb1_queued_fan_speed = pb1.get_running_fan_pwm()
            for i in range(1, 4):
                wall_name = f"Fan Wall {i}"
                self.fan_walls[i] = FanWall(i, wall_name, default_profile, powerboard_id=pb1.location, header_index=i - 1)
                self.fan_walls[i].set_service_reference(self)
                self.fan_walls[i].current_speed = pb1_queued_fan_speed[i - 1]
                logger.info(f"Initialized {wall_name}")
            app.timer(3.0, self.ping_powerboards)

        # Always initialize Auxiliary Fan 1; assign to powerboard 2 if available
        self.fan_walls[4] = FanWall(4, "Auxiliary Fan 1", default_profile)
        self.fan_walls[4].set_service_reference(self)
        if 2 in globals.powerboardDict:
            pb2 = globals.powerboardDict[2]
            pb2_queued_fan_speed = pb2.get_running_fan_pwm()
            self.fan_walls[4].powerboard_id = pb2.location
            self.fan_walls[4].header_index = 2
            self.fan_walls[4].current_speed = pb2_queued_fan_speed[2]
            logger.info("Initialized Auxiliary Fan 1 (assigned to PB2)")
        else:
            logger.info("Initialized Auxiliary Fan 1 (unassigned)")

        # Initialize unassigned auxiliary fans 2 and 3
        for zone_id, zone_name in [(5, "Auxiliary Fan 2"), (6, "Auxiliary Fan 3")]:
            self.fan_walls[zone_id] = FanWall(zone_id, zone_name, default_profile)
            self.fan_walls[zone_id].set_service_reference(self)
            logger.info(f"Initialized {zone_name} (unassigned)")

        # Apply loaded configuration after wall initialization
        self._apply_loaded_config()
    
    async def ping_powerboards(self) -> None:

        """Update variables from powerboards."""
        import globals

        if 1 in globals.powerboardDict:
            # Update powerboard state for rpm and wattage
            await run.io_bound(globals.powerboardDict[1].update_powerboard_state)
            # Update queued fan speed if it has changed
            if not self.fan_speed_current(1):
                self.update_powerboard_fan_speed(1)

        if 2 in globals.powerboardDict:
            await run.io_bound(globals.powerboardDict[2].update_powerboard_state)
            if not self.fan_speed_current(2):
                self.update_powerboard_fan_speed(2)
            
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
                    pb_speeds[loc]['speeds'][wall.header_index] = slider_list[slider_idx].value

                for loc, entry in pb_speeds.items():
                    pb_obj = entry['pb']
                    speeds = entry['speeds']
                    await run.io_bound(pb_obj.semaphore.acquire)
                    pb_obj.semaphore.release()
                    pb_obj.set_running_fan_pwm(*speeds)
                    ui.notify(
                        f"PWM updated {speeds[0]}, {speeds[1]}, {speeds[2]}",
                        position='bottom-right', type='positive', group=False
                    )
                    await run.io_bound(pb_obj.update_fan_speed, *speeds)
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

                aux_pwm = self.get_auxiliary_slider_value(slider_list)
                speeds = list(pb_obj.get_running_fan_pwm())
                speeds[wall.header_index] = aux_pwm

                ui.notify(
                    f"Auxiliary PWM updated {aux_pwm}",
                    position='bottom-right', type='positive', group=False
                )
                await run.io_bound(pb_obj.update_fan_speed, *speeds)
            except Exception as e:
                logger.error(f"Error updating auxiliary fan speed: {e}")
                ui.notify("Auxiliary fan speed update failed", position='bottom-right', type='negative', group=False)
            finally:
                self.update_aux_pwm_semaphore.release()

    async def set_fan_speed(self, row1, row2, row3, aux=100):
        """Set and save fan speed for both powerboards."""
        import globals
        
        # Set powerboard 1 fan speeds
        if 1 in globals.powerboardDict:
            pb1 = globals.powerboardDict[1]
            pb1.set_saved_fan_pwm(row1, row2, row3)

            await run.io_bound(
                globals.powerboardDict[1].set_fan_speed, 
                row1, 
                row2, 
                row3
            )
            pb1.set_running_fan_pwm(row1, row2, row3)
        # Set powerboard 2 fan speeds if it exists and has a slider value
        if 2 in globals.powerboardDict:
            pb2 = globals.powerboardDict[2]
            
            pb2.set_saved_fan_pwm(aux, aux, aux)
            
            await run.io_bound(
                pb2.set_fan_speed,
                aux,
                aux,
                aux
            )
            pb2.set_running_fan_pwm(aux, aux, aux)
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