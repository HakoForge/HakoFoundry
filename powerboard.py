import serial
import numpy as np
import threading
import logging
from typing import Tuple, Optional

# Configure logging
logger = logging.getLogger("foundry_logger")


class PowerboardError(Exception):
    """Custom exception for Powerboard communication errors."""
    pass


# ---2.2 Wattage Calculation Helpers---
# This dictionary stores the manual offsets derived from test data.
# Structure: {active_shunt_index: {expected_wattage: [s1_offset, s2_offset, s3_offset, s4_offset]}}
MANUAL_OFFSETS = {
    0: {  # Shunt 1 active
        108: [1, 0, 0, 0], 120: [1, 0, 0, 0], 132: [1, 0, 0, 0],
        144: [13, 0, 0, 0], 156: [13, 0, 0, 0], 168: [1, 0, 0, 0],
    },
    1: {  # Shunt 2 active
        24: [0, 1, 0, 0], 36: [0, 1, 0, 0], 48: [0, 1, 0, 0], 60: [0, 1, 0, 0],
        72: [0, 1, -1, 0], 84: [0, 1, -1, 0], 96: [0, 1, -1, 0], 108: [0, 1, -1, 0],
        120: [0, 1, -2, 0], 132: [0, 1, -2, 0], 144: [0, 1, -2, 0], 156: [0, 1, -2, 0],
        168: [0, 1, -2, 0],
    },
    2: {  # Shunt 3 active
        24: [0, 0, 1, 0], 36: [0, 0, 1, 0], 48: [0, 0, 1, 0],
        60: [0, -1, 1, 0], 72: [0, -1, 1, 0], 84: [0, -1, 1, 0], 96: [0, -1, 1, 0],
        108: [0, -1, 1, 0], 120: [0, -2, 1, 0], 132: [0, -2, 1, 0],
        144: [0, -2, -11, 0], 156: [0, -2, 1, 0], 168: [0, -3, 1, 0],
    },
    3: {  # Shunt 4 active
        12: [0, 0, 0, 1], 24: [0, 0, 0, 1], 36: [0, 0, 0, 1], 48: [0, 0, 0, 1],
        60: [0, 0, 0, 1], 72: [0, 0, 0, 1], 84: [0, 0, 0, 1], 96: [0, 0, 0, 1],
        108: [0, 0, 0, 1], 120: [0, 0, 0, 1], 132: [0, 0, 0, 1], 144: [0, 0, 0, 1],
        156: [0, 0, 0, 1], 168: [0, 0, 0, 1],
    }
}

def _apply_manual_offsets_22(wattages: np.ndarray) -> np.ndarray:
    """Apply final manual correction based on a lookup table of known errors."""
    active_shunt_index = int(np.argmax(wattages))
    active_wattage = int(wattages[active_shunt_index])

    # Find the closest expected wattage level (multiple of 12)
    expected_wattage_key = int(round(active_wattage / 12.0) * 12)

    # Lookup offset; default to zeros if not present
    offset = MANUAL_OFFSETS.get(active_shunt_index, {}).get(expected_wattage_key, [0, 0, 0, 0])
    return wattages + np.array(offset)

def _calculate_wattage_22(r1: float, r2: float, r3: float, r4: float, voltage: float = 12.0) -> list:
    """High-accuracy multivariate regression wattage calculation for HW 2.2.

    Returns a list of ints (wattages for shunts 1-4).
    """
    coefficients = np.array([
        [ 2.11e-02,  1.06e-03, -1.43e-06, -1.31e-06, -1.21e-06, -1.30e-09, -1.42e-09, -1.61e-09, -1.11e-10, -1.23e-10, -1.45e-10],
        [-2.23e-02, -1.21e-06,  1.06e-03, -1.11e-06, -1.01e-06, -1.21e-09, -1.31e-09, -1.43e-09, -1.21e-10, -1.33e-10, -1.55e-10],
        [-2.45e-02, -1.01e-06, -1.31e-06,  1.06e-03, -9.10e-07, -1.11e-09, -1.21e-09, -1.31e-09, -1.31e-10, -1.43e-10, -1.65e-10],
        [-2.81e-02, -8.10e-07, -1.11e-06, -1.21e-06,  1.06e-03, -1.01e-09, -1.11e-09, -1.21e-09, -1.41e-10, -1.53e-10, -1.75e-10]
    ])
    features = np.array([1, r1, r2, r3, r4, r1*r2, r1*r3, r1*r4, r2*r3, r2*r4, r3*r4])
    corrected_currents = coefficients @ features
    initial_wattages = voltage * corrected_currents

    # Round and clamp negative values to 0
    rounded_wattages = np.round(initial_wattages).astype(int)
    rounded_wattages[rounded_wattages < 0] = 0

    # Apply final manual offset correction
    final_wattages = _apply_manual_offsets_22(rounded_wattages)
    return final_wattages.astype(int).tolist()

class Powerboard:
    """
    Interface for communicating with hardware powerboard over serial connection.
    
    Handles fan control, power monitoring, and hardware metadata retrieval.
    All serial communication is protected by semaphore for thread safety.
    """
    
    # Constants
    SERIAL_TIMEOUT = 2
    BAUDRATE = 9600
    REFERENCE_VOLTAGE = 2.56
    ADC_MAX_VALUE = 1023
    TARGET_VOLTAGE = 12
    PWM_MAX_VALUE = 255

    # Serial commands
    COMMANDS = {
        'get_pwm': 'P:',
        'set_fan_speed': 'F:',
        'update_fan_speed': 'U:',
        'get_metadata': 'V:',
        'get_tach': 'T:',
        'get_wattage': 'W:',
        'get_jumper': 'J:'
    }

    def __init__(self, com_port: str):
        """Initialize powerboard connection and read initial state.
        
        Args:
            com_port: Serial port identifier (e.g., 'COM3', '/dev/ttyUSB0')
            
        Raises:
            PowerboardError: If connection fails or initial state cannot be read
        """
        # Initialize semaphore for thread-safe serial communication
        self.semaphore = threading.Semaphore()
        self._serial_instance = self._create_serial_connection(com_port)
        
        self._read_initial_metadata()
        self._read_initial_pwm_state()
        # Set the fan speed to the eeprom values
        self.update_fan_speed(self._current_fan_pwm[0], self._current_fan_pwm[1], self._current_fan_pwm[2])
        
        
        # Calibration constants
        if self._hardware_rev == '2.0':
            self.ADC_SLOPE = 3.574
            self.ADC_INTERCEPT = -1.375
        elif self._hardware_rev.startswith('2.1'):
            self.ADC_SLOPE = 3.284
            self.ADC_INTERCEPT = -1.069 
        elif self._hardware_rev.startswith('2.2'):
            self.ADC_SLOPE = 3.284
            self.ADC_INTERCEPT = -1.069 
        
        
        # Initialize other state variables
        self._current_fan_rpm: Optional[Tuple[int, int, int]] = None
        self._current_wattage: Optional[Tuple[float, float, float, float]] = None
        self._saved_fan_pwm: Tuple[int, int, int] = self._current_fan_pwm
        self._running_fan_pwm: Tuple[int, int, int] = self._current_fan_pwm

        self.row1_rpm: int = None
        self.row2_rpm: int = None
        self.row3_rpm: int = None

        self.watt_sec_1_2: int = None
        self.watt_sec_3_4: int = None

        # Raw serial responses for debug display
        self._raw_tach: Optional[str] = None
        self._raw_wattage: Optional[str] = None
        self._raw_pwm: Optional[str] = None
        
        # Update all powerboard state
        self.update_powerboard_state()

    def _create_serial_connection(self, com_port: str) -> serial.Serial:
        """Create and configure serial connection."""
        try:
            serial_instance = serial.Serial(
                port=com_port,
                baudrate=self.BAUDRATE,
                timeout=self.SERIAL_TIMEOUT
            )
            return serial_instance
        except (serial.SerialException, OSError) as e:
            raise PowerboardError(f"Failed to open serial port {com_port}: {e}")

    def _read_initial_metadata(self):
        """Read and store hardware metadata."""
        try:
            metadata = self.get_board_metadata()
            self._hardware_rev = metadata[0]
            self._firmware_ver = metadata[1]
            self._location = int(metadata[2])
        except (ValueError, IndexError) as e:
            raise PowerboardError(f"Failed to parse board metadata: {e}")

    def _read_initial_pwm_state(self):
        """Read initial PWM state from powerboard."""
        response = self._send_command(self.COMMANDS['get_pwm'])
        if not response:
            raise PowerboardError("Failed to read initial PWM state")
        self._raw_pwm = response
            
        try:
            pwm_values = [int(x) for x in response.split(',')]
            if len(pwm_values) != 3:
                raise ValueError("Expected 3 PWM values")
            # Convert from 0-255 to percentage
            if self.firmware_version == '2.3':
                pin1_pwm = self._convert_pwm_to_percent(255 - pwm_values[0])
                pin2_pwm = self._convert_pwm_to_percent(255 - pwm_values[1])
                pin3_pwm = self._convert_pwm_to_percent(255 - pwm_values[2])
            else:
                pin1_pwm = self._convert_pwm_to_percent(pwm_values[0])
                pin2_pwm = self._convert_pwm_to_percent(pwm_values[1])
                pin3_pwm = self._convert_pwm_to_percent(pwm_values[2])
            
            # Rearrange to represent (row1, row2, row3) respectively
            self._current_fan_pwm = (pin3_pwm, pin1_pwm, pin2_pwm)
            
        except (ValueError, IndexError) as e:
            raise PowerboardError(f"Failed to parse PWM response: {e}")

    def _send_command(self, command: str, params: str = "") -> str:
        """Send command to powerboard and return response.
        
        Args:
            command: Command string to send
            params: Optional parameters for command
            
        Returns:
            Response string from powerboard
            
        Raises:
            PowerboardError: If communication fails
        """
        full_command = f"{command}{params}\n"
        
        try:
            self._serial_instance.write(full_command.encode('utf-8'))
            response = self._serial_instance.readline().decode().strip()
            
            if not response:
                raise PowerboardError(f"No response to command: {command}")
                
            return response
            
        except (serial.SerialException, UnicodeDecodeError) as e:
            raise PowerboardError(f"Serial communication error: {e}")

    def _convert_pwm_to_percent(self, pwm_value: int) -> int:
        """Convert PWM value (0-255) to percentage (0-100)."""
        return round(pwm_value / self.PWM_MAX_VALUE * 100)

    def _validate_pwm_percentages(self, row1: int, row2: int, row3: int):
        """Validate PWM percentage values are within acceptable range."""
        for i, value in enumerate([row1, row2, row3], 1):
            if not isinstance(value, int) or not (0 <= value <= 100):
                raise ValueError(f"Row {i} PWM must be integer between 0-100, got: {value}")

    def set_fan_speed(self, row1: int, row2: int, row3: int):
        """Set fan speed using percentages and save to EEPROM.
        
        Args:
            row1, row2, row3: PWM percentages (0-100) for each fan row respectively
            
        Raises:
            PowerboardError: If command fails
            ValueError: If parameters are invalid
        """
        self._validate_pwm_percentages(row1, row2, row3)
        
        with self.semaphore:
            # Rearrange parameters to match hardware layout
            params = f"{row2},{row3},{row1}"
            response = self._send_command(self.COMMANDS['set_fan_speed'], params)
            
            logger.debug(f"Set fan speed response: {response}")
            self._current_fan_pwm = (row1, row2, row3)
            self._saved_fan_pwm = (row1, row2, row3)

    def update_fan_speed(self, row1: int, row2: int, row3: int):
        """Update fan speed temporarily without writing to EEPROM.
        
        Args:
            row1, row2, row3: PWM percentages (0-100) for each fan row respectively
            
        Raises:
            PowerboardError: If command fails
            ValueError: If parameters are invalid
        """
        self._validate_pwm_percentages(row1, row2, row3)
        
        with self.semaphore:
            # Rearrange parameters to match hardware layout
            if self.firmware_version == '2.2':
                params = f"{100 - row2},{100 - row3},{100 - row1}"
            else:
                params = f"{row2},{row3},{row1}"
            response = self._send_command(self.COMMANDS['update_fan_speed'], params)
            
            logger.debug(f"Update fan speed response: {response}")
            self._running_fan_pwm = (row1, row2, row3)

    def get_board_metadata(self) -> Tuple[str, str, str]:
        """Get board metadata including hardware revision, firmware version, and location.
        
        Returns:
            Tuple of (hardware_rev, firmware_ver, location)
            
        Raises:
            PowerboardError: If command fails
        """
        with self.semaphore:
            response = self._send_command(self.COMMANDS['get_metadata'])
            
        try:
            parts = response.split(',')
            if len(parts) != 3:
                raise ValueError("Expected 3 metadata fields")
                
            return (parts[0], parts[1], parts[2])
            
        except (ValueError, IndexError) as e:
            raise PowerboardError(f"Failed to parse metadata response: {e}")

    def get_fan_pwm(self) -> Tuple[int, int, int]:
        """Get current fan PWM percentages.
        
        Returns:
            Tuple of (row1_pwm, row2_pwm, row3_pwm) as percentages
        """
        return self._current_fan_pwm

    def get_saved_fan_pwm(self) -> Tuple[int, int, int]:
        """Get saved fan PWM percentages (last values written to EEPROM).
        
        Returns:
            Tuple of (row1_pwm, row2_pwm, row3_pwm) as percentages
        """
        return self._saved_fan_pwm

    def get_running_fan_pwm(self) -> Tuple[int, int, int]:
        """Get current running fan PWM percentages (may differ from saved).
        
        Returns:
            Tuple of (row1_pwm, row2_pwm, row3_pwm) as percentages
        """
        return self._running_fan_pwm

    def set_saved_fan_pwm(self, row1: int, row2: int, row3: int):
        """Set the saved PWM values (for UI state tracking)."""
        self._validate_pwm_percentages(row1, row2, row3)
        self._saved_fan_pwm = (row1, row2, row3)

    def set_running_fan_pwm(self, row1: int, row2: int, row3: int):
        """Set the running PWM values (for UI state tracking)."""
        self._validate_pwm_percentages(row1, row2, row3)
        self._running_fan_pwm = (row1, row2, row3)

    def get_fan_tach(self) -> Tuple[int, int, int]:
        """Get current fan RPM readings.
        
        Returns:
            Tuple of (row1_rpm, row2_rpm, row3_rpm)
            
        Raises:
            PowerboardError: If reading fails
        """
        if self._current_fan_rpm is None:
            raise PowerboardError("Fan RPM data not available")
        return self._current_fan_rpm

    def get_power_usage(self) -> Tuple[float, float, float, float]:
        """Get current power usage readings in watts.
        
        Returns:
            Tuple of (wattage1, wattage2, wattage3, wattage4)
            
        Raises:
            PowerboardError: If reading fails
        """
        if self._current_wattage is None:
            raise PowerboardError("Power usage data not available")
        return self._current_wattage

    def _update_fan_rpm(self):
        """Update fan RPM readings from powerboard."""
        with self.semaphore:
            response = self._send_command(self.COMMANDS['get_tach'])
        self._raw_tach = response
            
        try:
            rpm_values = [int(x) for x in response.split(',')]
            if len(rpm_values) != 3:
                raise ValueError("Expected 3 RPM values")
                
            # Analog readings to RPM
            self.row1_rpm = rpm_values[0] * 30
            self.row2_rpm = rpm_values[1] * 30
            self.row3_rpm = rpm_values[2] * 30
            
            self._current_fan_rpm = (self.row1_rpm, self.row2_rpm, self.row3_rpm)
            
        except (ValueError, IndexError) as e:
            raise PowerboardError(f"Failed to parse RPM response: {e}")

    def _update_power_usage(self):
        """Update power usage readings from powerboard."""
        with self.semaphore:
            response = self._send_command(self.COMMANDS['get_wattage'])
        self._raw_wattage = response
            
        try:
            analog_readings = [float(x) for x in response.split(',')]
            if len(analog_readings) != 4:
                raise ValueError("Expected 4 analog readings")
            # Use new high-accuracy calculation for HW 2.2 variants
            if str(self._hardware_rev).startswith('2.2') or str(self._hardware_rev).startswith('2.3'):
                r1, r2, r3, r4 = analog_readings
                wattages = _calculate_wattage_22(r1, r2, r3, r4, voltage=self.TARGET_VOLTAGE)
            else:
                # Calculation for other hardware revisions
                wattages = []
                for reading in analog_readings:
                    if reading == 0:
                        current = 0
                    else:
                        # Slope formula that compensates for low and high values
                        current = (reading - self.ADC_INTERCEPT) / self.ADC_SLOPE
                    wattages.append(current * self.TARGET_VOLTAGE)

            # Binded label varaibles
            # Swap indexes to represent physical sections
            self.watt_sec_1_2 = int(wattages[2] + wattages[3])
            self.watt_sec_3_4 = int(wattages[0] + wattages[1])

            self._current_wattage = tuple(wattages)
            
        except (ValueError, IndexError) as e:
            raise PowerboardError(f"Failed to parse wattage response: {e}")

    def update_powerboard_state(self):
        """Update all powerboard state including fan RPM and power usage.
        
        Raises:
            PowerboardError: If any update fails
        """
        try:
            self._update_fan_rpm()
            self._update_power_usage()
        except PowerboardError as e:
            raise PowerboardError(f"Failed to update powerboard state: {e}")

    def get_jumper_state(self) -> int:
        """Get jumper status for fan control mode.
        
        Returns:
            1 if jumper is on motherboard fan control, 0 if on powerboard fan control
            
        Raises:
            PowerboardError: If command fails
        """
        with self.semaphore:
            response = self._send_command(self.COMMANDS['get_jumper'])
            
        try:
            return int(response)
        except ValueError as e:
            raise PowerboardError(f"Failed to parse jumper state: {e}")

    def close(self):
        """Close the serial connection."""
        if hasattr(self, '_serial_instance') and self._serial_instance.is_open:
            self._serial_instance.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    @property
    def hardware_revision(self) -> str:
        """Get hardware revision."""
        return self._hardware_rev

    @property
    def firmware_version(self) -> str:
        """Get firmware version."""
        return self._firmware_ver

    @property
    def location(self) -> int:
        """Get powerboard location."""
        return self._location

    @property
    def is_connected(self) -> bool:
        """Check if serial connection is open."""
        return hasattr(self, '_serial_instance') and self._serial_instance.is_open

    def __repr__(self) -> str:
        """String representation of powerboard."""
        return (f"Powerboard(port={getattr(self._serial_instance, 'port', 'N/A')}, "
                f"hw_rev={getattr(self, '_hardware_rev', 'N/A')}, "
                f"fw_ver={getattr(self, '_firmware_ver', 'N/A')}, "
                f"location={getattr(self, '_location', 'N/A')})")
