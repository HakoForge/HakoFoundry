import json
import os
import xxhash
import logging
from typing import Optional, Dict, List, Any, Union
from dataclasses import dataclass
from pathlib import Path
import subprocess
import time


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("foundry_logger")


class FoundryStateError(Exception):
    """Custom exception for foundry state errors."""
    pass


class DriveStandbyError(Exception):
    """Raised when a drive is in standby/sleep and should not be woken."""
    pass


@dataclass
class DriveInfo:
    """Data class for drive information."""
    protocol: str
    model: str
    serial_num: str
    firmware_ver: str
    capacity: Optional[Union[int, str]]
    rotate_rate: Optional[int]
    power_cycle: Optional[int]
    on_time: Optional[int]
    temp: Optional[int]
    hash: int

    def __post_init__(self):
        if not hasattr(self, 'hash'):
            self.hash = xxhash.xxh3_64(self.serial_num).intdigest()


class Drive:
    """Represents a storage drive with S.M.A.R.T. data."""

    def __init__(self, protocol: str, model: str, serial_num: str, firmware_ver: str,
                 capacity: Optional[Union[int, str]], rotate_rate: Optional[int],
                 power_cycle: Optional[int], on_time: Optional[int], temp: Optional[int],
                 attribute_list: Optional[Any]):
        self.protocol = protocol
        self.model = model
        self.serial_num = serial_num
        self.firmware_ver = firmware_ver
        self.capacity = capacity
        self.rotate_rate = rotate_rate
        self.power_cycle = power_cycle
        self.on_time = on_time
        self.temp = temp
        self.attribute_list = attribute_list
        self.hash = xxhash.xxh3_64(serial_num).intdigest()

    def get_attribute_list(self) -> List[Dict[str, Any]]:
        """Get formatted attribute list based on protocol."""
        try:
            if self.protocol == 'ATA':
                if not self.attribute_list:
                    return []
                rows = []
                for attr in self.attribute_list:
                    rows.append({
                        'ID': attr.get("id", "Unknown"),
                        "Name": attr.get("name", "Unknown"),
                        "Value": attr.get("raw", {}).get("string", "Unknown")
                    })
                return rows

            elif self.protocol == 'NVMe':
                if not self.attribute_list:
                    return []
                rows = []
                for key, value in self.attribute_list.items():
                    if isinstance(value, list):
                        value = ", ".join(str(e) for e in value)
                    rows.append({"Name": key, "Value": str(value)})
                return rows

            else:
                return [{"Name": f"Protocol {self.protocol} not yet implemented", "Value": "N/A"}]

        except Exception as e:
            logger.error(f"Error getting attribute list for drive {self.serial_num}: {e}")
            return [{"Name": "Error", "Value": "Failed to parse attributes"}]

    def to_dict(self) -> Dict[str, Any]:
        """Convert drive to dictionary for serialization."""
        return {
            'protocol': self.protocol,
            'model': self.model,
            'serial_num': self.serial_num,
            'firmware_ver': self.firmware_ver,
            'capacity': self.capacity,
            'rotate_rate': self.rotate_rate,
            'power_cycle': self.power_cycle,
            'on_time': self.on_time,
            'temp': self.temp,
            'hash': self.hash
        }


class SmartCtlInterface:
    """Interface for smartctl operations."""
    @staticmethod
    def execute_command(args: str, timeout: int = 30) -> tuple[str, int]:
        """Execute smartctl command with timeout and error handling.

        Returns:
            Tuple of (stdout, returncode).
        """
        try:
            cmd = f"smartctl {args}"
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode == 127:
                raise FoundryStateError("smartctl command not found. Please install smartmontools.")
            if result.returncode < 0:  # Negative return codes indicate serious errors
                raise FoundryStateError(f"smartctl command failed: {result.stderr}")

            return result.stdout, result.returncode

        except subprocess.TimeoutExpired:
            logger.error(f"smartctl command timed out: {cmd}")
            raise FoundryStateError(f"Command timed out: {cmd}")
        except Exception as e:
            logger.error(f"Error executing smartctl command '{cmd}': {e}")
            raise FoundryStateError(f"Failed to execute smartctl: {e}")

    @classmethod
    def get_drive_ids(cls) -> List[str]:
        """Get list of available drive IDs."""
        try:
            output, _ = cls.execute_command("--scan")
            lines = output.strip().splitlines()
            device_list = [line.strip() for line in lines if line.strip()]
            logger.info(f"Found {len(device_list)} drives")
            return device_list
        except Exception as e:
            logger.error(f"Failed to get drive IDs: {e}")
            return []

    @classmethod
    def get_smart_data(cls, device_id: str, debug) -> Optional[Drive]:
        """Get S.M.A.R.T. data for a specific device."""
        try:
            device_path = device_id.split(' ')[0]
            json_output, returncode = cls.execute_command(f"-n standby --xall --json --device auto {device_path}")

            # Exit code 2 means the drive is in standby/sleep — do not wake it.
            if returncode == 2:
                logger.debug(f"Drive {device_path} is spun down, skipping query")
                raise DriveStandbyError(device_path)

            if not json_output.strip():
                logger.warning(f"No output from smartctl for device {device_path}")
                return None

            data = json.loads(json_output)
            drive = cls._parse_smart_data(data)

            if debug:
                if drive:
                    cls._save_raw_data(drive.model, json_output)

            return drive

        except DriveStandbyError:
            raise  # Let caller handle standby drives
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON for device {device_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to get S.M.A.R.T. data for {device_id}: {e}")
            return None

    @staticmethod
    def _parse_smart_data(data: Dict[str, Any]) -> Optional[Drive]:
        """Parse smartctl JSON data into Drive object."""
        try:
            device_info = data.get('device', {})
            protocol = device_info.get('protocol')

            if not protocol:
                logger.warning("No protocol found in S.M.A.R.T. data")
                return None

            # Common fields
            model = data.get('model_name') or data.get('scsi_model_name')
            serial_num = data.get('serial_number')
            firmware_ver = data.get('firmware_version')

            if not serial_num:
                logger.warning("No serial number found, skipping drive")
                return None

            # Capacity handling
            user_capacity = data.get('user_capacity', {})
            if protocol == 'SCSI':
                # Assuming 512 byte blocks
                format_blocks = lambda blocks, block_size=512: f"{blocks * block_size / (1024**4):.2f} TB" if blocks * block_size >= 1024**4 else f"{blocks * block_size / (1024**3):.2f} GB"
                capacity = format_blocks(user_capacity.get('blocks'))
            else:
                format_bytes = lambda bytes_count: f"{bytes_count / (1024**4):.2f} TB" if bytes_count >= 1024**4 else f"{bytes_count / (1024**3):.2f} GB"
                capacity = format_bytes(user_capacity.get('bytes'))

            # Common fields with safe extraction
            rotate_rate = data.get('rotation_rate')
            power_on_time = data.get('power_on_time', {})
            format_hours = lambda hours: f"{int(hours // 24)} day(s), {int(hours % 24)} hour(s)" if hours >= 24 else f"{int(hours % 24)} hour(s)"
            on_time = format_hours(power_on_time.get('hours')) if power_on_time else None
            temperature = data.get('temperature', {})
            temp = temperature.get('current') if temperature else None

            # Protocol-specific fields
            if protocol == 'ATA':
                power_cycle = data.get('power_cycle_count')
                ata_smart = data.get('ata_smart_attributes', {})
                attribute_list = ata_smart.get('table') if ata_smart else None

            elif protocol == 'SCSI':
                scsi_counter = data.get('scsi_start_stop_cycle_counter', {})
                power_cycle = scsi_counter.get('accumulated_start_stop_cycles') if scsi_counter else None
                if not temp:
                    report = data.get('scsi_environmental_reports', {})
                    temperature = report.get('temperature_1', {}) if report else None
                    temp = temperature.get('current') if temperature else None
                attribute_list = []

            elif protocol == 'NVMe':
                power_cycle = data.get('power_cycle_count')
                attribute_list = data.get('nvme_smart_health_information_log')

            else:
                logger.warning(f"Unsupported protocol: {protocol}")
                power_cycle = None
                attribute_list = None

            return Drive(
                protocol=protocol,
                model=model or "Unknown",
                serial_num=serial_num,
                firmware_ver=firmware_ver or "Unknown",
                capacity=capacity,
                rotate_rate=rotate_rate,
                power_cycle=power_cycle,
                on_time=on_time,
                temp=temp,
                attribute_list=attribute_list
            )

        except Exception as e:
            logger.error(f"Error parsing S.M.A.R.T. data: {e}")
            return None

    @staticmethod
    def _save_raw_data(model: str, json_output: str) -> None:
        """Save raw smartctl output for debugging."""
        try:
            safe_model = "".join(c for c in model if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{safe_model}_smartctl.json"

            with open(filename, "w", encoding='utf-8') as file:
                file.write(json_output)

        except Exception as e:
            logger.error(f"Failed to save raw data for {model}: {e}")


class DriveManager:
    """Manages drive detection and caching."""

    def __init__(self, debug, cache_duration: int = 300):  # 5 minutes cache
        self._drive_cache: Dict[int, Drive] = {}
        self._device_path_to_hash: Dict[str, int] = {}  # device path → drive hash
        self._last_scan_time: float = 0
        self._cache_duration = cache_duration
        self._debug = debug

        if self._debug:
            logger.setLevel(logging.DEBUG)

    def get_drives(self, force_refresh: bool = False) -> Dict[int, Drive]:
        """Get all drives with caching."""
        current_time = time.time()

        if (not force_refresh and
            self._drive_cache and
            (current_time - self._last_scan_time) < self._cache_duration):
            logger.info("Using cached drive data")
            return self._drive_cache.copy()

        logger.info("Scanning for drives...")
        self._drive_cache = self._scan_drives()
        self._last_scan_time = current_time

        return self._drive_cache.copy()

    def _scan_drives(self) -> Dict[int, Drive]:
        """Scan for all drives and return dictionary."""
        drives = {}
        drive_ids = SmartCtlInterface.get_drive_ids()

        for device_id in drive_ids:
            device_path = device_id.split(' ')[0]
            try:
                drive = SmartCtlInterface.get_smart_data(device_id, self._debug)
                if drive:
                    drives[drive.hash] = drive
                    self._device_path_to_hash[device_path] = drive.hash
                    logger.info(f"Added drive: {drive.model} ({drive.serial_num})")
                else:
                    logger.warning(f"Failed to get data for device: {device_id}")

            except DriveStandbyError:
                # Drive is asleep — preserve its cached data so it isn't evicted.
                cached_hash = self._device_path_to_hash.get(device_path)
                if cached_hash is not None and cached_hash in self._drive_cache:
                    drives[cached_hash] = self._drive_cache[cached_hash]
                    logger.debug(f"Drive {device_path} is in standby, retaining cached data")
                else:
                    logger.debug(f"Drive {device_path} is in standby, no cached data available yet")

            except Exception as e:
                logger.error(f"Error processing device {device_id}: {e}")
                continue

        logger.info(f"Found {len(drives)} valid drives")
        return drives

    def get_drive_by_hash(self, drive_hash: int) -> Optional[Drive]:
        """Get specific drive by hash."""
        return self._drive_cache.get(drive_hash)

    def refresh_drives(self) -> Dict[int, Drive]:
        """Force refresh of drive data."""
        return self.get_drives(force_refresh=True)

    def refresh_drives_dict(self, existing_drives: Dict[int, Drive]) -> None:
        """
        Refresh an existing drives dictionary in-place, preserving object references where possible.

        Args:
            existing_drives: Dictionary to update with current drive information
        """
        try:
            # Get fresh drive data
            fresh_drives = self.get_drives(force_refresh=True)

            # Track changes for logging
            updated_count = 0
            added_count = 0
            removed_hashes = []

            # Update existing drives in-place and add new ones
            for drive_hash, fresh_drive in fresh_drives.items():
                if drive_hash in existing_drives:
                    # Update existing Drive object in-place to preserve references
                    existing_drive = existing_drives[drive_hash]
                    if self._update_drive_inplace(existing_drive, fresh_drive):
                        updated_count += 1
                else:
                    # Add new drive
                    existing_drives[drive_hash] = fresh_drive
                    added_count += 1

            # Remove drives that are no longer present
            for drive_hash in list(existing_drives.keys()):
                if drive_hash not in fresh_drives:
                    removed_hashes.append(drive_hash)
                    del existing_drives[drive_hash]

            logger.info(f"Refreshed drives dictionary: {updated_count} updated, "
                       f"{added_count} added, {len(removed_hashes)} removed")

        except Exception as e:
            logger.error(f"Error refreshing drives dictionary: {e}")
            raise

    def _update_drive_inplace(self, existing_drive: Drive, fresh_drive: Drive) -> bool:
        """Update an existing Drive object with fresh data in-place.

        Args:
            existing_drive: The existing Drive object to update
            fresh_drive: The fresh Drive object with new data

        Returns:
            bool: True if any changes were made, False otherwise
        """
        changed = False

        # List of attributes that might change over time
        updatable_attrs = ['on_time', 'temp', 'attribute_list']

        for attr in updatable_attrs:
            fresh_value = getattr(fresh_drive, attr)
            existing_value = getattr(existing_drive, attr)

            if fresh_value != existing_value:
                setattr(existing_drive, attr, fresh_value)
                changed = True

        return changed


class Backplane:
    """Represents a backplane that holds drives."""

    # Class-level configuration for backplane types
    BACKPLANE_CONFIGS = {
        "STD4HDD": {"slots": 4, "type": "HDD"},
        "STD12SSD": {"slots": 12, "type": "SSD"},
        "SML2+2": {"slots": 4, "type": "Mixed"}
    }

    def __init__(self, product: str):
        if product not in self.BACKPLANE_CONFIGS:
            raise ValueError(f"Unknown backplane product: {product}")

        self.product = product
        self.config = self.BACKPLANE_CONFIGS[product]
        self.drives_hashes = [None] * self.config["slots"]

    def insert_drive(self, drive_hash: int, index: int) -> None:
        """Insert drive at specific index."""
        if not (0 <= index < len(self.drives_hashes)):
            raise IndexError(f"Invalid drive index: {index}")
        self.drives_hashes[index] = drive_hash

    def remove_drive(self, drive_hash: int) -> None:
        """Remove drive by hash."""
        try:
            index = self.drives_hashes.index(drive_hash)
            self.drives_hashes[index] = None
        except ValueError:
            logger.warning(f"Drive hash {drive_hash} not found in backplane")

    def is_empty(self) -> bool:
        """Check if backplane has no drives."""
        return all(drive_hash is None for drive_hash in self.drives_hashes)

    def get_drive_count(self) -> int:
        """Get number of installed drives."""
        return sum(1 for drive_hash in self.drives_hashes if drive_hash is not None)

    def to_json(self) -> Dict[str, Any]:
        """Convert to JSON-serializable format."""
        return {
            "product": self.product,
            "drives": self.drives_hashes
        }

class Chassis:
    """Manages chassis layout and backplane configuration."""

    DEFAULT_CONFIG_FILE = "config/layout_config.json"
    MAX_BACKPLANES = 12

    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file or self.DEFAULT_CONFIG_FILE
        self.product: Optional[str] = None
        self.backplanes: List[Optional[Backplane]] = [None] * self.MAX_BACKPLANES
        self.show_model = True
        self.show_sn = True
        self.hide_multi_curve_dialog = False  # User preference for multi-curve dialog
        # Preferences
        self.chassis_orientation = False  # False for normal, True for inverted
        self.units = "C"  # Temperature units: "C" for Celsius, "F" for Fahrenheit
        self.pb_swap = False # Powerboard swap preference

        if config_file:
            self._load_config()

    def _load_config(self) -> None:
        """Load configuration from file."""
        try:
            config_path = Path(self.config_file)
            if not config_path.exists():
                logger.info(f"Config file {self.config_file} not found, creating default config")
                # Create default config and save it for future use
                self.save_config()
                return

            with open(config_path, "r", encoding='utf-8') as file:
                config:dict = json.load(file)

            self.product = config.get("product")
            backplanes_data = config.get("backplanes", [])
            options = config.get("options", {})
            self.show_model = options.get("show_model", True)
            self.show_sn = options.get("show_sn", True)
            self.hide_multi_curve_dialog = options.get("hide_multi_curve_dialog", False)
            # Handle both old string format and new boolean format
            orientation_value = options.get("chassis_orientation", "normal")
            if isinstance(orientation_value, str):
                self.chassis_orientation = orientation_value == "inverted"
            else:
                self.chassis_orientation = bool(orientation_value)
            self.units = options.get("units", "C")
            self.pb_swap = options.get("pb_swap", False)

            # Ensure we have the right number of backplane slots
            while len(backplanes_data) < self.MAX_BACKPLANES:
                backplanes_data.append(None)

            self.backplanes = []
            for bp_data in backplanes_data[:self.MAX_BACKPLANES]:
                if bp_data is None:
                    self.backplanes.append(None)
                else:
                    try:
                        backplane = Backplane(bp_data["product"])
                        backplane.drives_hashes = bp_data.get("drives", [None] * backplane.config["slots"])
                        self.backplanes.append(backplane)
                    except Exception as e:
                        logger.error(f"Error loading backplane: {e}")
                        self.backplanes.append(None)

            logger.info(f"Loaded chassis config: {self.product}")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config file: {e}")
            self._reset_to_defaults()
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            self._reset_to_defaults()

    def _reset_to_defaults(self) -> None:
        """Reset to default configuration."""
        self.product = None
        self.backplanes = [None] * self.MAX_BACKPLANES

    def set_product(self, product: str) -> None:
        """Set chassis product type."""
        self.product = product
        self.save_config()

    def set_model_display(self, value):
        """Set model display option."""
        self.show_model = value
        self.save_config()

    def get_model_display(self):
        """Set model display option."""
        return self.show_model

    def set_sn_display(self, value):
        """Return serial number display option."""
        self.show_sn = value
        self.save_config()

    def get_sn_display(self):
        """Return model display option."""
        return self.show_sn

    def set_units(self, units: str) -> None:
        """Set temperature units (C or F)."""
        if units not in ["C", "F"]:
            raise ValueError("Units must be 'C' for Celsius or 'F' for Fahrenheit")
        self.units = units
        self.save_config()

    def get_units(self) -> str:
        """Get temperature units."""
        return self.units

    def set_pb_swap(self, value: bool) -> None:
        """Set powerboard swap option."""
        self.pb_swap = value
        self.save_config()

    def get_pb_swap(self) -> bool:
        """Get powerboard swap option."""
        return self.pb_swap

    def chassis_is_inverted(self) -> bool:
        """Get chassis orientation setting. Returns True if inverted, False if normal."""
        return getattr(self, 'chassis_orientation', False)

    def set_chassis_inverted(self, inverted: bool) -> None:
        """Set chassis orientation setting."""
        self.chassis_orientation = bool(inverted)
        self.save_config()

    def get_product(self) -> Optional[str]:
        """Get chassis product type."""
        return self.product

    def insert_backplane(self, card, product: str) -> Backplane:
        """Insert backplane at specified card."""
        card_index = card.index if hasattr(card, 'index') else card
        if not (0 <= card_index < self.MAX_BACKPLANES):
            raise IndexError(f"Invalid backplane index: {card_index}")

        backplane = Backplane(product)
        self.backplanes[card_index] = backplane
        self.save_config()
        return backplane

    def remove_backplane(self, card) -> None:
        """Remove backplane at specified card."""
        card_index = card.index if hasattr(card, 'index') else card
        if not (0 <= card_index < self.MAX_BACKPLANES):
            raise IndexError(f"Invalid backplane index: {card_index}")

        self.backplanes[card_index] = None
        self.save_config()

    def get_backplanes(self) -> List[Optional[Backplane]]:
        """Get all backplanes."""
        return self.backplanes.copy()

    def is_empty(self) -> bool:
        """Check if chassis has no backplanes."""
        return all(bp is None for bp in self.backplanes)

    def insert_drive(self, card, drive_selection: str, drive_index: int) -> None:
        """Insert drive into backplane."""
        card_index = card.index if hasattr(card, 'index') else card
        if not (0 <= card_index < self.MAX_BACKPLANES):
            raise IndexError(f"Invalid card index: {card_index}")

        backplane = self.backplanes[card_index]
        if backplane is None:
            raise ValueError(f"No backplane at index {card_index}")

        # Parse drive serial number from selection string
        try:
            serial_num = drive_selection.split()[-1][1:-1]  # Extract from format "Model (SN123)"
            drive_hash = xxhash.xxh3_64(serial_num).intdigest()
            backplane.insert_drive(drive_hash, drive_index)
            self.save_config()
        except Exception as e:
            logger.error(f"Error inserting drive: {e}")
            raise ValueError(f"Invalid drive selection format: {drive_selection}")

    def remove_drive(self, card, drive_hash: int) -> None:
        """Remove drive from backplane."""
        card_index = card.index if hasattr(card, 'index') else card
        if not (0 <= card_index < self.MAX_BACKPLANES):
            raise IndexError(f"Invalid card index: {card_index}")

        backplane = self.backplanes[card_index]
        if backplane is None:
            raise ValueError(f"No backplane at index {card_index}")

        backplane.remove_drive(drive_hash)
        self.save_config()

    def reset_chassis(self) -> None:
        """Reset chassis to empty state."""
        self.product = None
        self.backplanes = [None] * self.MAX_BACKPLANES
        self.save_config()

    def clear_all_backplanes(self) -> None:
        """Clear all backplanes while keeping chassis product and other settings."""
        self.backplanes = [None] * self.MAX_BACKPLANES
        self.save_config()

    def save_config(self) -> None:
        """Save configuration to file."""
        try:
            config_data = {
                "product": self.product,
                "backplanes": [bp.to_json() if bp is not None else None for bp in self.backplanes],
                "options": {
                        "show_model": self.show_model,
                        "show_sn": self.show_sn,
                        "hide_multi_curve_dialog": self.hide_multi_curve_dialog,
                        "chassis_orientation": self.chassis_orientation,
                        "units": self.units,
                        "pb_swap": self.pb_swap
                    }
            }

            # Ensure config directory exists
            config_path = Path(self.config_file)
            config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_file, "w", encoding='utf-8') as file:
                json.dump(config_data, file, indent=4)

            logger.info("Configuration saved successfully")

        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            raise FoundryStateError(f"Failed to save configuration: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Get chassis statistics."""
        total_backplanes = sum(1 for bp in self.backplanes if bp is not None)
        total_drives = sum(bp.get_drive_count() for bp in self.backplanes if bp is not None)

        return {
            "product": self.product,
            "total_backplanes": total_backplanes,
            "total_drives": total_drives,
            "max_backplanes": self.MAX_BACKPLANES
        }
