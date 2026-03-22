from nicegui import ui, run, app
import json
from datetime import datetime
import asyncio
import os
import logging
from authentication import require_auth
import page_layout
import fan_profile_manager
import globals

# Configure logging
logger = logging.getLogger("foundry_logger")

def process_fan_curves_data(curves_data, active_curve, visibility=None):
    """
    Python function that processes multiple fan curve datasets.
    """
    return fan_profile_manager.process_fan_curves_data(curves_data, active_curve, visibility)

@require_auth
def fanCurvePage():
    # Use the global backend instance
    backend = globals.fan_profile_service
    
    # Initialize global fan backend if not already done
    if backend is None:
        globals.initFanProfileBackend()
        backend = globals.fan_profile_service
    
    # ALWAYS reload from config file when page is loaded to ensure fresh data
    logger.debug("Fan curve page loaded - reloading from config file")
    backend.reload_from_config()
    logger.debug("Config reloaded, page state reset to saved values")
    
    # Helper function to get default sensor
    def get_default_sensor():
        """Get the default sensor to use when none is assigned (returns None to avoid auto-assignment)."""
        return None  # Always return None to prevent automatic sensor assignment
    
    # Helper function to format sensor display names
    def format_sensor_display_name(sensor_name):
        """Format sensor name for display (remove 'Drives.' prefix for cleaner display)."""
        if sensor_name.startswith('Drives.'):
            return sensor_name[7:]  # Remove "Drives." prefix for display
        return sensor_name
    
    # Helper function to get internal sensor name from display name
    def get_internal_sensor_name(display_name, available_sensors):
        """Convert display name back to internal sensor name."""
        # If it's "Drives", keep as is
        if display_name == 'Drives':
            return display_name
        
        # Check if this display name corresponds to a drive monitor
        for sensor in available_sensors:
            if sensor.startswith('Drives.') and sensor[7:] == display_name:
                return sensor
        
        # Otherwise, it's a regular sensor name
        return display_name
    
    # Helper function to get available sensors
    def get_available_sensors():
        """Get the list of available temperature sensors with display-friendly names."""
        try:
            available_sensors = backend.get_available_temperature_sensors()
            
            # Check if current curve has a drive monitor assigned
            current_curve_has_drives = (selected_curve and selected_curve.sensor and 
                                      selected_curve.sensor.startswith('Drives.'))
            
            # Filter out drive monitors that belong to other curves
            # Only show the current curve's drive monitor (if it has one) and regular sensors
            filtered_sensors = []
            current_curve_id = selected_curve.id if selected_curve else ""
            
            # Get temperature backend for curve-specific filtering
            temp_backend = globals.temp_sensor_service
            
            # Always ensure current curve's assigned sensor is included
            current_sensor = selected_curve.sensor if selected_curve and selected_curve.sensor else None
            
            for sensor in available_sensors:
                if sensor.startswith('Drives.'):
                    # This is a drive monitor - only include it if it belongs to the current curve
                    drive_monitor_name = sensor[7:]  # Remove "Drives." prefix
                    
                    should_include = False
                    
                    # Always include if this is the current curve's assigned sensor (defensive programming)
                    if current_sensor == sensor:
                        should_include = True
                    elif temp_backend:
                        # Check if this drive monitor belongs to the current curve ID
                        drive_monitor = temp_backend.get_drive_monitor(drive_monitor_name)
                        if drive_monitor and drive_monitor.curve_id == current_curve_id:
                            should_include = True
                        # Skip drive monitors that belong to other curves or have no curve association
                    else:
                        # Fallback: if no temp backend, skip all drive monitors for safety
                        pass
                    
                    if should_include:
                        display_name = format_sensor_display_name(sensor)
                        if display_name not in filtered_sensors:  # Prevent duplicates
                            filtered_sensors.append(display_name)
                else:
                    # Regular sensor - always include
                    if sensor not in filtered_sensors:  # Prevent duplicates
                        filtered_sensors.append(sensor)
            
            # Always include "Drives" as an option if current curve doesn't already have drives configured
            # This allows each curve to have its own unique drive monitor
            if filtered_sensors:
                # Always include "None" as the first option
                result_sensors = ['None']
                
                # Add all the filtered sensors
                result_sensors.extend(filtered_sensors)
                
                if not current_curve_has_drives:
                    result_sensors.append('Drives')
                
                # Final safety check: ensure current curve's sensor is included (defensive programming)
                if current_sensor and current_sensor.startswith('Drives.'):
                    current_display_name = format_sensor_display_name(current_sensor)
                    if current_display_name not in result_sensors:
                        logger.warning(f"Adding missing current sensor '{current_display_name}' to available options")
                        result_sensors.append(current_display_name)
                
                return result_sensors
            else:
                return ['None'] + (['Drives'] if not current_curve_has_drives else [])
        except Exception as e:
            logger.warning(f" Error getting available sensors: {e}")
            return ['None'] + (['Drives'] if not current_curve_has_drives else [])
    
    # Helper function to check if real hardware sensors are available
    def has_real_sensors():
        """Check if there are actual hardware sensors available (not just fallback)."""
        try:
            available_sensors = backend.get_available_temperature_sensors()
            return len(available_sensors) > 0
        except Exception as e:
            logger.warning(f" Error checking for real sensors: {e}")
            return False
    
    # Helper function to safely set dropdown options
    def safe_set_temp_selection_options(options, value):
        """Safely set temperature selection options, ensuring value is in options list."""
        if not ui_elements.get('temp_selection'):
            return
        
        # Ensure the value is in the options list
        if value not in options:
            logger.warning(f"Temperature sensor '{value}' not found in options {options}, adding it")
            # Add the missing value to the options list
            options = list(options) + [value]
        
        ui_elements['temp_selection'].set_options(options, value=value)
    
    # Helper function to update temperature selection dropdown
    def update_temperature_selection(sensor_value):
        """Update the temperature selection dropdown and handle disabled state."""
        if ui_elements.get('temp_selection'):
            sensor_display_name = format_sensor_display_name(sensor_value)
            ui_elements['temp_selection'].set_value(sensor_display_name)
            
    # Page-specific state variables - these stay on the page
    selected_profile = None  # Will be set during initialization
    selected_curve = None    # Will be set during initialization
    has_unsaved_changes = False  # Track structural changes (add/remove curves)
    updating_dropdown = False  # Flag to prevent callback loops
    
    first_profile_id = backend.get_first_profile_id()
    if first_profile_id:
        selected_profile = backend.get_profile(first_profile_id)
    else:
        # No profiles exist, create a default one
        default_profile_id = backend.add_profile()
        selected_profile = backend.get_profile(default_profile_id)
        
        # Set the default profile's first curve sensor to None (no auto-assignment)
        first_curve_id = backend.get_first_curve_id(default_profile_id)
        if first_curve_id:
            first_curve = selected_profile.get_curve(first_curve_id)
            first_curve.sensor = None
            logger.info(f"Set default profile's first curve to None (no automatic sensor assignment): {first_curve.name}")
    
    # Ensure we have an active profile before proceeding
    if not selected_profile:
        raise RuntimeError("Failed to initialize active profile")
    
    first_curve_id = backend.get_first_curve_id(selected_profile.id)
    if first_curve_id:
        selected_curve = selected_profile.get_curve(first_curve_id)
    
    # Store UI element references
    ui_elements = {
        'active_profile_select': None,
        'active_curve_select': None,
        'temp_selection': None,
        'configure_drives_btn': None
    }
    
    async def check_for_changes():
        """Check if the current chart data differs from the saved profile data."""
        
        # First check for structural changes (add/remove curves)
        if has_unsaved_changes:
            return True
            
        try:
            # Get current chart data
            data_json = await ui.run_javascript('getCurrentDataForPython()', timeout=5.0)
            if not data_json:
                logger.debug(" No chart data received, assuming no changes")
                return False
                
            chart_data = json.loads(data_json)
            chart_curves = chart_data['curves']
            
            # Compare with saved profile data
            saved_curves = selected_profile.get_all_curves()
            
            logger.debug(f" Comparing {len(chart_curves)} chart curves with {len(saved_curves)} saved curves")
            
            # Check if number of curves differs
            if len(chart_curves) != len(saved_curves):
                logger.debug(f" Curve count mismatch - chart: {len(chart_curves)}, saved: {len(saved_curves)}")
                return True
            
            # Check each curve for differences
            for curve_name, chart_curve in chart_curves.items():
                # Find the saved curve by name (since saved_curves is keyed by ID)
                saved_curve = None
                for curve_id, curve_obj in saved_curves.items():
                    if curve_obj.name == curve_name:
                        saved_curve = curve_obj
                        break
                
                if not saved_curve:
                    logger.debug(f" Chart curve '{curve_name}' not found in saved curves")
                    return True  # Chart has a curve that doesn't exist in saved data
                
                # Compare curve data points
                chart_points = chart_curve['data']
                saved_points = saved_curve._data
                
                if len(chart_points) != len(saved_points):
                    return True
                
                # Compare each point
                for i, (chart_point, saved_point) in enumerate(zip(chart_points, saved_points)):
                    if (chart_point['x'] != saved_point['x'] or 
                        chart_point['y'] != saved_point['y']):
                        logger.debug(f" Data point mismatch in '{curve_name}' at index {i}: chart({chart_point['x']}, {chart_point['y']}) vs saved({saved_point['x']}, {saved_point['y']})")
                        return True
                
                # Compare sensor assignments
                chart_sensor = chart_curve.get('sensor', '')
                saved_sensor = saved_curve.sensor if saved_curve.sensor else ''
                if chart_sensor != saved_sensor:
                    return True
                
                # Compare curve names
                if chart_curve['name'] != saved_curve.name:
                    return True
            
            logger.debug(" No changes detected between chart and saved data")
            return False  # No changes detected
            
        except Exception as e:
            logger.info(f"Error checking for changes: {e}")
            return True  # Assume changes if we can't check properly

    async def load_profile_data_to_chart(profile):
        """Load a profile's curve data into the chart."""
        try:
            # Prepare all curve data for efficient loading
            profile_data = {}
            for curve_id, curve_obj in selected_profile.get_all_curves().items():
                sensor = curve_obj.sensor if curve_obj.sensor else ''  # Use empty string instead of fallback sensor
                # Use the curve name as the key, not the ID
                profile_data[curve_obj.name] = {
                    'data': curve_obj._data,
                    'sensor': sensor,
                    'name': curve_obj.name  # Ensure the curve name is included for the chart legend
                }
            
            # Get the active curve name from the page state
            active_curve_name = selected_curve.name if selected_curve else (list(profile_data.keys())[0] if profile_data else '')
            
            # Load all profile data in one operation to prevent flickering
            profile_data_json = json.dumps(profile_data)
            profile_name = selected_profile.get_name()
            
            await ui.run_javascript(f'loadProfileData({profile_data_json}, "{active_curve_name}", "{profile_name}")')
            
            logger.info(f"Successfully loaded profile data for: {selected_profile.get_name()}")
            return True
        except Exception as e:
            logger.error(f" Failed loading profile data: {e}")
            return False

    def save_to_config_file():
        """Save current state to config file."""
        logger.debug(" save_to_config_file() called")
        result = backend.save_to_config()
        logger.debug(f" backend.save_to_config() returned: {result}")
        return result
    
    def reload_from_config_file():
        """Reload state from config file, discarding any unsaved changes."""
        return backend.reload_from_config()

    async def reset_all_flags():
        """Reset all unsaved changes flags."""
        nonlocal has_unsaved_changes
        has_unsaved_changes = False
        try:
            await ui.run_javascript('updateUnsavedChangesStatus(false)')
        except Exception as e:
            logger.warning(f" Could not reset JavaScript unsaved changes flag: {e}")

    async def set_unsaved_changes(value=True):
        """Set unsaved changes flag for structural changes (add/remove curves)."""
        nonlocal has_unsaved_changes
        has_unsaved_changes = value
        try:
            await ui.run_javascript(f'updateUnsavedChangesStatus({str(value).lower()})')
        except Exception as e:
            logger.warning(f" Could not update JavaScript unsaved changes status: {e}")

    async def get_unsaved_changes_status():
        """Return the current unsaved changes status (internal use - no JavaScript update)."""
        
        # Check for structural changes OR differences between current chart state and saved state
        chart_has_changes = await check_for_changes()
        
        # Return true if there are structural changes OR detected chart data changes
        result = has_unsaved_changes or chart_has_changes
        
        return result

    async def check_and_update_unsaved_changes():
        """Check unsaved changes status and update JavaScript (for event handler)."""
        result = await get_unsaved_changes_status()
        
        # Update the JavaScript side with the current status
        try:
            await ui.run_javascript(f'window.updateUnsavedChangesStatus({str(result).lower()})')
        except Exception as e:
            logger.warning(f" Could not update JavaScript unsaved changes status: {e}")
        
        return result

    async def save_current_profile_data():
        """Save the current chart data back to the Python profile object and config file."""
        try:
            logger.debug(f" Starting save_current_profile_data for profile: {selected_profile.get_name()}")
            data_json = await ui.run_javascript('getCurrentDataForPython()', timeout=5.0)
            logger.debug(f" Got data from JavaScript: {data_json[:200] if data_json else 'None'}...")
            
            if not data_json:
                logger.debug(" No data received from JavaScript - returning False")
                return False
                
            data = json.loads(data_json)
            curves_data = data.get('curves', {})
            logger.debug(f" Parsed curves data, found {len(curves_data)} curves")
            
            if not curves_data:
                logger.debug(" No curves data found in response - returning False")
                return False
            
            # Update each curve in the active profile with the chart data
            curves_updated = 0
            for curve_name, curve_data in curves_data.items():
                logger.debug(f" Processing curve: {curve_name}")
                # Find the curve by name
                curve_obj = None
                for curve_id, curve in selected_profile.get_all_curves().items():
                    if curve.name == curve_name:
                        curve_obj = curve
                        break
                
                if curve_obj:
                    curve_obj._data = curve_data['data']
                    curve_obj.sensor = curve_data.get('sensor', None)  # Preserve None instead of auto-assigning
                    curve_obj.name = curve_data['name']
                    curves_updated += 1
                    logger.debug(f" Updated curve {curve_name} with {len(curve_data['data'])} data points")
                else:
                    logger.debug(f" Warning - could not find curve object for name: {curve_name}")
            
            logger.debug(f" Updated {curves_updated} curves out of {len(curves_data)} in JavaScript data")
            
            # Save to config file
            logger.debug(" Calling save_to_config_file()")
            config_saved = save_to_config_file()
            logger.debug(f" save_to_config_file() returned: {config_saved}")
            
            if config_saved:
                # Reset unsaved changes flag on successful save
                await reset_all_flags()
                logger.debug(f" Successfully saved profile data and config for: {selected_profile.get_name()}")
                return True
            else:
                logger.debug(f" Failed to save config file for profile: {selected_profile.get_name()}")
                return False
                
        except Exception as e:
            logger.debug(f" Error saving profile data: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def open_drive_selection_dialog(existing_sensor=None):
        """Open a dialog for selecting drives with chassis layout similar to overview page.
        
        Args:
            existing_sensor: If provided, pre-populate dialog with settings from this sensor
        """
        
        # Check if this is the first time configuring drives and user hasn't disabled the dialog
        is_new_configuration = existing_sensor is None or not existing_sensor.startswith('Drives.')
        show_explanation = is_new_configuration and should_show_drive_selection_dialog()
        
        # Create dialog with chassis layout spanning full viewport
        with ui.dialog() as drive_dialog, ui.card().classes('p-0').style('width: 95dvw; height: 95dvh; max-width: none; max-height: none;'):
            # Drive selection state
            selected_drives = set()
            
            # Load existing configuration if editing
            existing_monitor = None
            if existing_sensor and existing_sensor.startswith('Drives.'):
                temp_backend = globals.temp_sensor_service
                if temp_backend and selected_curve:
                    # Find drive monitor for current curve ID
                    existing_monitor = temp_backend.get_drive_monitor(selected_curve.id)
                    if existing_monitor:
                        # Pre-populate with existing drive selection
                        selected_drives.update(existing_monitor.get_selected_drives())
                        logger.info(f"Editing existing drive monitor for curve {selected_curve.name} with {len(selected_drives)} drives")
            
            # Create the explanation overlay container that will be shown initially if needed
            explanation_container = None
            if show_explanation:
                with ui.element('div').classes('fixed inset-0 flex items-center justify-center z-50').style('background-color: rgba(0, 0, 0, 0.7);') as explanation_container:
                    with ui.card().classes('p-6 max-w-2xl mx-4'):
                        ui.html('<h3 class="text-xl font-semibold mb-4">Drive Temperature Monitoring</h3>')
                        
                        ui.html('''
                        <div class="space-y-4 text-sm">
                            <p class="text-base">
                                <strong>You're about to configure drive temperature monitoring!</strong> 
                                This allows your fan curve to respond to drive temperatures.
                            </p>
                            
                            <div class="bg-blue-50 border-l-4 border-blue-400 p-4 rounded">
                                <h4 class="font-semibold text-blue-800 mb-2">How Drive Monitoring Works:</h4>
                                <ul class="list-disc list-inside space-y-1 text-blue-700">
                                    <li><strong>Select Drives:</strong> Click on drive buttons to include them in temperature monitoring</li>
                                    <li><strong>Aggregation Modes:</strong> Choose "Average" or "Maximum" mode for the selected drives.</li>
                                    <li><strong>Real-time Updates:</strong> Selected drives are continuously monitored for temperature changes</li>
                                    <li><strong>Flexible Selection:</strong> You can select just a few critical drives or all drives in the system</li>
                                </ul>
                            </div>
                            
                            <div class="bg-green-50 border-l-4 border-green-400 p-4 rounded">
                                <h4 class="font-semibold text-green-800 mb-2">Selection Tips:</h4>
                                <div class="list-disc list-inside text-green-700 space-y-1">
                                    <li><strong>For Storage Arrays:</strong> Select all drives for comprehensive cooling</li>
                                    <li><strong>For Mixed Workloads:</strong> Select only the drives under heavy use</li>
                                    <li><strong>For Cache Drives:</strong> Use "Maximum" mode to prioritize the hottest drive</li>
                                    <li><strong>Multiple Profiles:</strong> Different drives can be assigned to different fan walls with fan profiles</li>
                                </div>
                            </div>
                            
                            <div class="bg-yellow-50 border-l-4 border-yellow-400 p-4 rounded">
                                <p class="text-yellow-800">
                                    <strong>Next Steps:</strong> In the chassis layout, click drive buttons to select them. 
                                    Selected drives will be highlighted in yellow. Use "Select All" for convenience or choose specific drives based on your cooling needs.
                                </p>
                            </div>
                        </div>
                        ''')
                        
                        # Checkbox to not show again
                        hide_dialog_checkbox = ui.checkbox('Don\'t show this explanation again', value=False).classes('mt-4')
                        
                        with ui.row().classes('w-full justify-end gap-2 mt-6'):
                            def proceed_with_selection():
                                # Save preference if checkbox is checked
                                if hide_dialog_checkbox.value:
                                    save_drive_selection_dialog_preference(True)
                                
                                # Hide the explanation overlay
                                explanation_container.set_visibility(False)
                            
                            ui.button('Got it, Select Drives', icon='storage', on_click=proceed_with_selection).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                            ui.button('Cancel', on_click=drive_dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
            
            def toggle_drive_selection(button):
                """Toggle selection state of a drive button."""
                if hasattr(button, 'assigned_drive') and button.assigned_drive:
                    drive_hash = button.assigned_drive.hash
                    if drive_hash in selected_drives:
                        # Deselect
                        selected_drives.remove(drive_hash)
                        button.classes('border-white', remove='border-[#ffdd00]')
                    else:
                        # Select
                        selected_drives.add(drive_hash)
                        button.classes('border-[#ffdd00]', remove='border-white')
            
            def select_all_drives():
                """Select all drives that have assigned drives."""
                for card in drive_cards:
                    for button in card.buttons:
                        if hasattr(button, 'assigned_drive') and button.assigned_drive:
                            drive_hash = button.assigned_drive.hash
                            if drive_hash not in selected_drives:
                                selected_drives.add(drive_hash)
                                button.classes('border-[#ffdd00]', remove='border-white')
            
            def deselect_all_drives():
                """Deselect all drives."""
                selected_drives.clear()
                for card in drive_cards:
                    for button in card.buttons:
                        button.classes('border-white', remove='border-[#ffdd00]')
            
            # Chassis layout container with control buttons on the right
            drive_cards = []  # Store references to cards for selection operations
            
            with ui.element('div').classes('flex w-full p-4 gap-4 h-full'):
                # Chassis container on the left with viewport-relative sizing
                with ui.element('div').classes('pseudo-extend-drives flex-1 h-full').style('min-height: 840px;'):
                    current_chassis = globals.layoutState.get_product()
                    
                    if current_chassis is None:
                        ui.label('No chassis layout available. Please set up chassis in Overview page first.').classes('text-center text-gray-500 p-8')
                    else:
                        # Create chassis layout for drives only
                        layout_configs = {
                            "Hako-Core": {
                                "grid_cols": 12,
                                "std_cards": 9,
                                "sml_cards": 3
                            },
                            "Hako-Core Mini": {
                                "grid_cols": 8,
                                "std_cards": 6,
                                "sml_cards": 2
                            },
                            "HF-L1": {
                                "grid_cols": 4,
                                "std_cards": 3,
                                "sml_cards": 1
                            }
                        }
                        
                        config = layout_configs[current_chassis]
                        
                        # Create grid container for drives only with viewport-relative sizing like overview page
                        with ui.element('div').classes(
                            f'grid grid-cols-{config["grid_cols"]} gap-1'
                        ).style('height: 100%; width: 100%; grid-template-rows: 26% 26% 26% 22%; min-height: 60dvh;'):
                            
                            # Create backplane cards (similar to overview page setup_backplane_buttons)
                            if not globals.layoutState.is_empty():
                                backplane_list = globals.layoutState.get_backplanes()
                                
                                # Standard cards
                                for i, bp in enumerate(backplane_list[:config["std_cards"]]):
                                    card_widget = create_drive_card(i, bp, toggle_drive_selection, "std", selected_drives)
                                    drive_cards.append(card_widget)
                                
                                # Small cards
                                start_idx = config["std_cards"]
                                for i, bp in enumerate(backplane_list[start_idx:start_idx + config["sml_cards"]]):
                                    card_widget = create_drive_card(i + start_idx, bp, toggle_drive_selection, "sml", selected_drives)
                                    drive_cards.append(card_widget)
                            else:
                                ui.label('No backplanes configured. Please configure backplanes in Overview page first.').classes('col-span-full text-center text-gray-500 p-8')
                
                # Control buttons on the right side
                with ui.element('div').classes('flex flex-col justify-center gap-4 p-4').style('min-width: 200px;'):
                    ui.button('Select All', icon='select_all', on_click=select_all_drives).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    ui.button('Deselect All', icon='clear', on_click=deselect_all_drives).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    ui.separator()
                    
                    # Pre-populate aggregation mode if editing existing monitor
                    default_aggregation = 'Avg of selected'
                    if existing_monitor:
                        default_aggregation = 'Avg of selected' if existing_monitor.aggregation_mode == 'average' else 'Max of selected'
                    
                    aggregation_radio = ui.radio(['Avg of selected', 'Max of selected'], value=default_aggregation).props('color=yellowhako')
                    # Spacer to separate control and action buttons
                    ui.element('div').style('height: 20px;')
                    
                    # Action buttons
                    async def apply_drive_selection():
                        nonlocal updating_dropdown  # Access the flag from parent scope
                        if selected_drives:
                            # Import the DriveTemperatureMonitor class
                            from temperature_sensor_service import DriveTemperatureMonitor
                            
                            # Get aggregation mode from radio selection
                            aggregation_mode = "average" if aggregation_radio.value == 'Avg of selected' else "maximum"
                            
                            temp_backend = globals.temp_sensor_service
                            if temp_backend:
                                # Remove any existing drive monitors for this curve first
                                curve_id = selected_curve.id if selected_curve else None
                                if curve_id:
                                    existing_count = temp_backend.remove_drive_monitors_for_curve(curve_id)
                                    if existing_count > 0:
                                        action_text = "Updated"
                                    else:
                                        action_text = "Created"
                                else:
                                    action_text = "Created"
                                
                                # Create a clean name for this drive monitor
                                # No need to include curve ID in the name since it's used as the key
                                monitor_name = f"Drive Temp ({len(selected_drives)} drives, {aggregation_mode})"
                                
                                # Create the drive temperature monitor with curve ID association
                                drive_monitor = DriveTemperatureMonitor(
                                    name=monitor_name,
                                    aggregation_mode=aggregation_mode,
                                    curve_id=curve_id
                                )
                                
                                # Set the selected drives
                                drive_monitor.set_drives(list(selected_drives))
                                
                                # Add to the temperature backend
                                temp_backend.add_drive_monitor(drive_monitor)
                                
                                # Save the temperature backend configuration immediately
                                temp_config_saved = temp_backend.save_configuration()
                                if temp_config_saved:
                                    logger.info(f"Temperature backend configuration saved after adding drive monitor")
                                else:
                                    logger.warning(f" Failed to save temperature backend configuration")
                                
                                # Update the sensor source name to reference the drive monitor
                                # Keep internal "Drives." prefix for system compatibility
                                sensor_name = f"Drives.{monitor_name}"
                                
                                # Update the active curve's sensor
                                selected_curve.sensor = sensor_name
                                
                                # Update the JavaScript side
                                await ui.run_javascript(f'updateCurveSensor("{selected_curve.name}", "{sensor_name}")')
                                logger.info(f"Drive sensor updated (unsaved): {selected_curve.name} -> {sensor_name}")
                                
                                # Refresh the temperature selection dropdown options
                                if ui_elements.get('temp_selection'):
                                    # Get updated list of sensors
                                    updated_options = get_available_sensors()
                                    # Convert internal sensor name to display name for the dropdown
                                    sensor_display_name = format_sensor_display_name(sensor_name)
                                    # Use flag to prevent callback loop when updating dropdown
                                    updating_dropdown = True
                                    safe_set_temp_selection_options(updated_options, sensor_display_name)
                                    updating_dropdown = False
                                
                                # Update configure drives button visibility (should now be visible)
                                if ui_elements.get('configure_drives_btn'):
                                    ui_elements['configure_drives_btn'].set_visibility(True)
                                
                                # Refresh the temperature display to show the new monitor
                                if ui_elements.get('refresh_temp_display'):
                                    ui_elements['refresh_temp_display']()
                                
                                # Mark as unsaved changes
                                await set_unsaved_changes(True)
                                
                                ui.notify(f'{action_text} drive temperature monitor: {monitor_name}', type='positive')
                                logger.info(f"{action_text} drive monitor with {len(selected_drives)} drives using {aggregation_mode} aggregation")
                            else:
                                ui.notify('Temperature backend not available', type='negative')
                        else:
                            ui.notify('No drives selected', type='info')
                        drive_dialog.close()
                    
                    ui.button('Apply', on_click=apply_drive_selection).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    ui.button('Cancel', on_click=drive_dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
        
        drive_dialog.open()

    def create_drive_card(index, backplane, toggle_callback, card_type, pre_selected_drives=None):
        """Create a drive card similar to overview page but for drive selection only."""
        
        if pre_selected_drives is None:
            pre_selected_drives = set()
        
        # Create cards with proper sizing that matches overview page approach
        if card_type == "std":
            # Standard cards use col-span-3 and aspect-ratio like overview page
            card = ui.element('div').classes('col-span-4 p-0 flex border').style('border-color: #0f0f0f; width: 100%; height: 100%; border-radius: 8px;')
        else:
            # Small cards use col-span-3 and different aspect ratio like overview page
            card = ui.element('div').classes('col-span-4 p-0 flex h-full border').style('border-color: #0f0f0f; width: 100%; max-height: 100%; border-radius: 8px;')
        
        # Add necessary attributes to match StdPlaceHolderCard/SmlPlaceHolderCard
        card.index = index
        card.buttons = []
        
        if backplane:
            # Setup drive buttons similar to overview page
            setup_drive_buttons_for_selection(card, backplane, index, toggle_callback, pre_selected_drives)
        
        return card

    def setup_drive_buttons_for_selection(card, backplane, index, toggle_callback, pre_selected_drives=None):
        """Set up drive buttons for selection (simplified version of overview page setup_backplane_buttons)."""
        
        if pre_selected_drives is None:
            pre_selected_drives = set()
        
        card.clear()
        cage = ""  # Can be default or reversed
        backplane_type = backplane.product

        # Create simplified drive button
        def create_simple_drive_button(card, button_index, drive_hash, button_style="hdd"):
            """Create a simplified drive button for selection."""
            button = ui.button().props('flat color="white" size="11px"').classes('border-solid border-2 truncate')
            
            # Set button styling based on type - using percentage heights that scale with window like overview page
            if button_style == "hdd":
                button.classes('w-full my-0.5').style('height: 24%; margin: 1px 0;')
            elif button_style == "std_ssd":
                button.classes('w-full my-0.5 p-0.5 px-1').style('height: 15.7%; margin: 1px 0;')
            elif button_style == "sml_ssd":
                button.classes('w-2/3 my-0.5 px-2 p-1').style('height: 18%; margin: 1px 0;')
            
            button.button_index = button_index
            button.card = card
            
            # Assign drive if available
            if drive_hash is not None and drive_hash in globals.drivesList:
                button.assigned_drive = globals.drivesList.get(drive_hash)
                
                # Check if this drive is pre-selected and apply highlighting
                if drive_hash in pre_selected_drives:
                    button.classes('border-[#ffdd00]', remove='border-white')
                else:
                    button.classes('border-white')
                
                with button.style('background-color: #1a1a1a;'):
                    with ui.row().classes('justify-center gap-2 w-full overflow-hidden'):
                        ui.label().bind_text_from(button.assigned_drive, 'temp', lambda temp: f"{temp}°C").classes('flex-shrink-0 text-xs')
                        ui.label(button.assigned_drive.model).classes('overflow-hidden whitespace-nowrap text-ellipsis text-white text-xs')
            else:
                button.assigned_drive = None
                button.disable()  # Disable empty buttons
                button.style('background-color: #1a1a1a; border-color: #0f0f0f;')  # Darker background and border for disabled buttons
            
            return button

        backplane_configs = {
            "STD4HDD": {
                "buttons": 4,
                "button_style": "hdd",
                "layout": "single_column"
            },
            "STD12SSD": {
                "buttons": 12,
                "button_style": "std_ssd",
                "layout": "two_column"
            },
            "SML2+2": {
                "buttons": 4,
                "button_styles": ["hdd", "hdd", "sml_ssd", "sml_ssd"],
                "layout": "mixed"
            }
        }
        
        config = backplane_configs.get(backplane_type)
        if not config:
            return
            
        with card:
            if config["layout"] == "single_column":
                with ui.element('div').classes(f'h-full w-full flex items-center justify-center p-1'):
                    for i in range(config["buttons"]):
                        button = create_simple_drive_button(card, i, backplane.drives_hashes[i], config["button_style"])
                        button.on('click', lambda b=button: toggle_callback(b))
                        card.buttons.append(button)
                        
            elif config["layout"] == "two_column":
                with ui.element('div').classes(f'grid grid-cols-2 gap-1 flex items-center justify-center h-full w-full p-1'):
                    with ui.element('col1').classes('col-span-1 h-full w-full'):
                        for i in range(6):
                            button = create_simple_drive_button(card, i, backplane.drives_hashes[i], config["button_style"])
                            button.on('click', lambda b=button: toggle_callback(b))
                            card.buttons.append(button)
                    with ui.element('col2').classes('col-span-1 h-full'):
                        for i in range(6, 12):
                            button = create_simple_drive_button(card, i, backplane.drives_hashes[i], config["button_style"])
                            button.on('click', lambda b=button: toggle_callback(b))
                            card.buttons.append(button)
                            
            elif config["layout"] == "mixed":
                with ui.element('div').classes(f'h-full w-full flex items-center justify-center p-1 gap-0.5'):
                    for i in range(4):
                        button_style = config["button_styles"][i]
                        button = create_simple_drive_button(card, i, backplane.drives_hashes[i], button_style)
                        button.on('click', lambda b=button: toggle_callback(b))
                        if i >= 2:  # SSD buttons
                            button.props('no-wrap')
                        else:  # HDD buttons - using percentage height that scales with window like overview page
                            button.style('height: 28%; margin: 1px 0;')
                        card.buttons.append(button)

    async def handle_sensor_change(value):
        """A dedicated async handler for sensor changes."""
        nonlocal selected_curve, selected_profile, updating_dropdown
        
        # Skip processing if we're updating the dropdown programmatically
        if updating_dropdown:
            return
        
        # Convert display name back to internal sensor name
        available_sensors = backend.get_available_temperature_sensors()
        
        # Handle explicit None selection
        if value == 'None':
            internal_sensor_name = None
        else:
            internal_sensor_name = get_internal_sensor_name(value, available_sensors)
        
        # Update configure drives button visibility first (always do this)
        if ui_elements.get('configure_drives_btn'):
            if internal_sensor_name and internal_sensor_name.startswith('Drives.'):
                ui_elements['configure_drives_btn'].set_visibility(True)
            else:
                ui_elements['configure_drives_btn'].set_visibility(False)
        
        # Check if "Drives" was selected - open drive selection dialog for new configuration
        if value == 'Drives':
            # Reset to previous value first, then open dialog
            current_sensor = selected_curve.sensor if selected_curve.sensor else None
            current_display_name = format_sensor_display_name(current_sensor) if current_sensor else 'None'
            if ui_elements.get('temp_selection'):
                updating_dropdown = True
                ui_elements['temp_selection'].set_value(current_display_name)
                updating_dropdown = False
            await open_drive_selection_dialog()
            return
        
        # For existing drive monitors, don't change anything - the user will use the Configure Drives button
        if internal_sensor_name and internal_sensor_name.startswith('Drives.'):
            # Update the sensor but don't open dialog
            current_sensor = selected_curve.sensor if selected_curve.sensor else None
            if current_sensor == internal_sensor_name:
                # No actual change, don't mark as unsaved
                return
            
            # Update the active curve's sensor in memory only
            selected_curve.sensor = internal_sensor_name
            
            # Update the JavaScript side
            await ui.run_javascript(f'updateCurveSensor("{selected_curve.name}", "{internal_sensor_name}")')
            logger.info(f"Drive sensor updated (unsaved): {selected_curve.name} -> {internal_sensor_name}")
            
            # Mark as unsaved changes
            await set_unsaved_changes(True)
            return
        
        # Check if the sensor value is actually changing
        current_sensor = selected_curve.sensor if selected_curve.sensor else None
        if current_sensor == internal_sensor_name:
            # No actual change, don't mark as unsaved
            return
        
        # Update the active curve's sensor in memory only (don't save to profile yet)
        selected_curve.sensor = internal_sensor_name
        
        # Update the JavaScript side
        sensor_for_js = internal_sensor_name if internal_sensor_name else ""
        await ui.run_javascript(f'updateCurveSensor("{selected_curve.name}", "{sensor_for_js}")')
        logger.info(f"Sensor updated (unsaved): {selected_curve.name} -> {internal_sensor_name}")
        
        # Mark as unsaved changes
        await set_unsaved_changes(True)

    async def handle_active_curve_change(e):
            """A dedicated async handler to safely update UI on active curve change."""
            nonlocal selected_curve
            
            # Find and set the new active curve from the profile by name
            if selected_profile:
                # Get the curve ID by name, then get the curve object
                curve_id = backend.get_curve_id_by_name(selected_profile.id, e.value)
                if curve_id:
                    selected_curve = selected_profile.get_curve(curve_id)
                    logger.info(f"Active curve changed to: {selected_curve.name}")
                    
                    # Update temperature selection to reflect the new active curve's sensor
                    if ui_elements.get('temp_selection'):
                        sensor_value = selected_curve.sensor if selected_curve.sensor else None
                        sensor_display_name = format_sensor_display_name(sensor_value) if sensor_value else 'None'
                        # Refresh the temperature selection options for the new curve
                        # This ensures "Drives" is available if the curve doesn't have a drive monitor
                        updated_options = get_available_sensors()
                        safe_set_temp_selection_options(updated_options, sensor_display_name)
                        logger.info(f"Updated sensor selection to: {sensor_display_name}")
                    
                    # Update configure drives button visibility based on new curve's sensor
                    if ui_elements.get('configure_drives_btn'):
                        current_sensor = selected_curve.sensor if selected_curve.sensor else None
                        if current_sensor and current_sensor.startswith('Drives.'):
                            ui_elements['configure_drives_btn'].set_visibility(True)
                        else:
                            ui_elements['configure_drives_btn'].set_visibility(False)
                    
                    # When switching active curve update chart
                    await ui.run_javascript(f'setActiveCurve("{e.value}")')
                else:
                    logger.info(f"Error: Could not find curve with name '{e.value}'")
            #await update_curve_controls()

    async def handle_active_profile_change(e):
        """Handler for when the active profile is changed."""
        nonlocal selected_profile, selected_curve
        
        # If we're trying to switch to the same profile, just return
        if selected_profile.get_name() == e.value:
            return
            
        # Store the target profile for later use
        target_profile = backend.get_profile_by_name(e.value)
        
        # Check if there are any changes before showing the dialog
        has_changes = await get_unsaved_changes_status()
        
        if not has_changes:
            # No changes detected, switch directly without dialog
            await switch_to_profile(target_profile)
            logger.info(f"No changes detected, switched directly to: {target_profile.get_name()}")
            return
        
        # Show save confirmation dialog only if there are changes
        with ui.dialog() as save_dialog, ui.card().classes('p-6'):
            ui.label(f'Save Current Profile?')
            ui.label('You have unsaved changes in the current profile. Do you want to save them before switching?')
            
            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Save', on_click=lambda: save_dialog.submit('save')).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                ui.button('Discard Changes', on_click=lambda: save_dialog.submit('discard')).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                ui.button('Cancel', on_click=lambda: save_dialog.submit('cancel')).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
        
        # Handle dialog result
        async def handle_save_dialog():
            result = await save_dialog
            
            if result == 'save':
                logger.debug(f" Save and switch chosen")
                # Save current profile data
                save_success = await save_current_profile_data()
                logger.debug(f" save_current_profile_data() returned: {save_success}")
                
                if save_success:
                    ui.notify(f'Saved changes to "{selected_profile.get_name()}"', type='positive')
                    logger.debug(f" Successfully saved, now switching to profile: {target_profile.get_name()}")
                    # Switch to new profile only if save was successful
                    await switch_to_profile(target_profile)
                else:
                    ui.notify(f'Failed to save changes to "{selected_profile.get_name()}"', type='negative')
                    logger.debug(f" Save failed for profile: {selected_profile.get_name()}")
                    # Don't switch profiles if save failed
                    # Reset the select value to the current profile since we're not switching
                    ui_elements['active_profile_select'].set_value(selected_profile.get_name())
            
            elif result == 'discard':
                logger.debug(f" Discard changes and switch chosen")
                # Store the target profile name before reloading
                target_profile_name = target_profile.get_name()
                current_profile_name = selected_profile.get_name()
                
                # Reload from config file to revert all changes
                reload_success = backend.reload_from_config()
                
                if reload_success:
                    # Reset the unsaved changes flag since we just reloaded from saved state
                    await reset_all_flags()
                    
                    # Update profile options (but don't set the value yet - let switch_to_profile handle it)
                    ui_elements['active_profile_select'].set_options(backend.get_profile_names())
                    
                    ui.notify('Reverted all changes from saved file', type='info')
                
                # Get fresh reference to target profile after reload
                fresh_target_profile = backend.get_profile_by_name(target_profile_name)
                
                # Switch to new profile (this will properly update all UI elements)
                await switch_to_profile(fresh_target_profile)
            
            else:  # result == 'cancel' or result is None (dialog dismissed)
                logger.debug(f" Dialog cancelled or dismissed (result: {result})")
                # Reset the select value to the current profile
                ui_elements['active_profile_select'].set_value(selected_profile.get_name())
        
        # Open dialog and handle result
        save_dialog.open()
        await handle_save_dialog()
    
    async def switch_to_profile(target_profile):
        """Actually perform the profile switch."""
        nonlocal selected_profile, selected_curve
        
        selected_profile = target_profile
        
        # Set the first curve of the new profile as active
        first_curve_id = backend.get_first_curve_id(selected_profile.id)
        if first_curve_id:
            selected_curve = selected_profile.get_curve(first_curve_id)
        
        # Update UI elements - including the active profile select
        ui_elements['active_profile_select'].set_value(selected_profile.get_name())
        ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=selected_curve.name)
        if ui_elements.get('temp_selection'):
            sensor_value = selected_curve.sensor if selected_curve.sensor else None
            sensor_display_name = format_sensor_display_name(sensor_value) if sensor_value else 'None'
            # Refresh temperature selection options for the new profile's active curve
            updated_options = get_available_sensors()
            safe_set_temp_selection_options(updated_options, sensor_display_name)
        
        # Update configure drives button visibility based on new profile's active curve
        if ui_elements.get('configure_drives_btn'):
            current_sensor = selected_curve.sensor if selected_curve.sensor else None
            if current_sensor and current_sensor.startswith('Drives.'):
                ui_elements['configure_drives_btn'].set_visibility(True)
            else:
                ui_elements['configure_drives_btn'].set_visibility(False)
        
        # ALWAYS load the profile's data into the chart to ensure synchronization
        # This ensures chart data matches Python objects even after reverts
        await load_profile_data_to_chart(selected_profile)
        
        # Set the first curve as active in JavaScript
        await ui.run_javascript(f'setActiveCurve("{selected_curve.name}")')
        logger.info(f"Switched to profile: {selected_profile.get_name()}")

    async def add_profile():
        """Add a new fan profile."""
        nonlocal selected_profile, selected_curve
        
        new_profile_id = backend.add_profile()
        
        # Set the new profile as active locally
        selected_profile = backend.get_profile(new_profile_id)
        first_curve_id = backend.get_first_curve_id(new_profile_id)
        if first_curve_id:
            selected_curve = selected_profile.get_curve(first_curve_id)
            
            # Set temperature source to None for new curves
            selected_curve.sensor = None
            # Set sensor in profile's curve collection (use curve ID)
            selected_profile.set_curve_sensor(first_curve_id, None)
            logger.info(f"New curve '{selected_curve.name}' created with no temperature source assigned")
        
        # Update UI dropdowns
        ui_elements['active_profile_select'].set_options(backend.get_profile_names(), value=selected_profile.get_name())
        ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=selected_curve.name)
        
        if ui_elements.get('temp_selection'):
            sensor_value = selected_curve.sensor if selected_curve.sensor else None
            sensor_display_name = format_sensor_display_name(sensor_value) if sensor_value else 'None'
            # Refresh temperature selection options for the new profile
            updated_options = get_available_sensors()
            safe_set_temp_selection_options(updated_options, sensor_display_name)
        
        # Update configure drives button visibility for new profile
        if ui_elements.get('configure_drives_btn'):
            current_sensor = selected_curve.sensor if selected_curve.sensor else None
            if current_sensor and current_sensor.startswith('Drives.'):
                ui_elements['configure_drives_btn'].set_visibility(True)
            else:
                ui_elements['configure_drives_btn'].set_visibility(False)
        
        # Load the new profile's data into the chart
        await load_profile_data_to_chart(selected_profile)
        await ui.run_javascript(f'setActiveCurve("{selected_curve.name}")')
        
        # Save to config file immediately
        config_saved = save_to_config_file()
        if config_saved:
            ui.notify(f'Profile "{selected_profile.get_name()}" created and saved to config', type='positive')
            logger.info(f"Added new profile: {selected_profile.get_name()} and saved to config")
        else:
            ui.notify(f'Profile "{selected_profile.get_name()}" created but failed to save config', type='warning')
            logger.info(f"Added new profile: {selected_profile.get_name()} but failed to save config")

    def remove_profile():
        """Remove the active profile (if more than one exists)."""
        nonlocal selected_profile, selected_curve
        
        def get_assigned_fan_walls(profile_name):
            """Get list of fan walls assigned to the given profile (only automatic/profile-controlled walls)."""
            assigned_walls = []
            try:
                fan_service = globals.fan_control_service
                if fan_service and hasattr(fan_service, 'fan_walls'):
                    for wall_id, wall in fan_service.fan_walls.items():
                        if wall.assigned_profile == profile_name and not wall.manual:
                            assigned_walls.append(wall.name)
            except Exception as e:
                logger.warning(f" Error checking fan wall assignments: {e}")
            return assigned_walls
        
        if len(backend.profiles) > 1:
            current_profile_name = selected_profile.get_name()
            assigned_walls = get_assigned_fan_walls(current_profile_name)
            
            # Show confirmation dialog
            with ui.dialog() as dialog, ui.card().classes('p-6'):
                ui.html(f'<h3 class="text-lg font-semibold mb-4">Delete Profile?</h3>')
                ui.html(f'<p class="mb-4">Are you sure you want to permanently delete the profile <strong>"{current_profile_name}"</strong>?</p>')
                
                # Show fan wall assignments if any
                if assigned_walls:
                    ui.html('<p class="mb-4 text-orange-400"><strong>Warning:</strong> The following fan walls are currently assigned to this profile:</p>')
                    for wall_name in assigned_walls:
                        ui.html(f'<p class="ml-4 mb-1 text-orange-300">• {wall_name}</p>')
                    ui.html('<p class="mb-4 text-orange-400">These fan walls will be automatically reassigned to the next available profile.</p>')

                with ui.row().classes('w-full justify-end gap-2'):
                    async def confirm_delete():
                        nonlocal selected_profile, selected_curve
                        
                        # Remove any associated drive monitors from all curves in this profile before removing the profile
                        temp_backend = globals.temp_sensor_service
                        total_removed_monitors = 0
                        if temp_backend:
                            # Get all curves in the profile that's being deleted
                            all_curves = selected_profile.get_all_curves()
                            for curve_id, curve_obj in all_curves.items():
                                removed_count = temp_backend.remove_drive_monitors_for_curve(curve_id)
                                total_removed_monitors += removed_count
                                if removed_count > 0:
                                    logger.info(f"Removed {removed_count} drive monitor(s) associated with curve: {curve_obj.name}")
                            
                            # Save the temperature backend configuration if any drive monitors were removed
                            if total_removed_monitors > 0:
                                temp_config_saved = temp_backend.save_configuration()
                                if temp_config_saved:
                                    logger.info(f"Temperature backend configuration saved after removing {total_removed_monitors} drive monitor(s)")
                                else:
                                    logger.warning(f" Failed to save temperature backend configuration")
                        
                        # Remove from backend (use profile ID)
                        backend.remove_profile(selected_profile.id)
                        
                        # Switch to the first remaining profile
                        first_remaining_profile_id = backend.get_first_profile_id()
                        if first_remaining_profile_id:
                            selected_profile = backend.get_profile(first_remaining_profile_id)
                            first_curve_id = backend.get_first_curve_id(selected_profile.id)
                            if first_curve_id:
                                selected_curve = selected_profile.get_curve(first_curve_id)
                        
                        # Update UI
                        ui_elements['active_profile_select'].set_options(backend.get_profile_names(), value=selected_profile.get_name())
                        ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=selected_curve.name)
                        
                        if ui_elements.get('temp_selection'):
                            sensor_value = selected_curve.sensor if selected_curve.sensor else None
                            sensor_display_name = format_sensor_display_name(sensor_value) if sensor_value else 'None'
                            # Refresh temperature selection options for the new profile's active curve
                            updated_options = get_available_sensors()
                            safe_set_temp_selection_options(updated_options, sensor_display_name)
                        
                        # Update configure drives button visibility based on new profile's active curve
                        if ui_elements.get('configure_drives_btn'):
                            current_sensor = selected_curve.sensor if selected_curve.sensor else None
                            if current_sensor and current_sensor.startswith('Drives.'):
                                ui_elements['configure_drives_btn'].set_visibility(True)
                            else:
                                ui_elements['configure_drives_btn'].set_visibility(False)
                        
                        # Refresh the temperature display to remove any deleted drive monitors
                        if ui_elements.get('refresh_temp_display'):
                            ui_elements['refresh_temp_display']()
                        
                        # Load the new profile's data into the chart
                        await load_profile_data_to_chart(selected_profile)
                        await ui.run_javascript(f'setActiveCurve("{selected_curve.name}")')
                        
                        # Save changes to JSON config file
                        config_saved = save_to_config_file()
                        if config_saved:
                            ui.notify(f'Profile "{current_profile_name}" deleted and saved to config', type='positive')
                            logger.info(f"Removed profile: {current_profile_name} and saved to config")
                        else:
                            ui.notify(f'Profile "{current_profile_name}" deleted but failed to save config', type='warning')
                            logger.info(f"Removed profile: {current_profile_name} but failed to save config")
                        
                        dialog.close()
                    
                    def cancel_delete():
                        dialog.close()
                    
                    ui.button('Delete', on_click=confirm_delete).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    ui.button('Cancel', on_click=cancel_delete).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
            
            dialog.open()
        else:
            ui.notify('Cannot remove the last profile', type='warning')

    def edit_profile_name():
        """Edit the name of the active profile."""
        with ui.dialog() as dialog, ui.card():
            ui.label('Edit Profile Name').classes('text-lg font-semibold mb-2')
            previous_name = selected_profile.get_name()
            name_input = ui.input('Profile Name', value=previous_name).classes('w-64')
            
            with ui.row():
                def save_profile_name():
                    nonlocal selected_profile
                    new_name = name_input.value.strip()
                    if new_name and new_name != previous_name:
                        # Check for duplicate names
                        if new_name in backend.get_profile_names():
                            ui.notify(f'Profile name "{new_name}" already exists. Please choose a different name.', type='warning')
                            return  # Don't close dialog, let user try again
                        
                        # Use backend method to rename profile
                        #backend.rename_profile(previous_name, new_name)
                        selected_profile.set_name(new_name)
                        
                        # Update the UI
                        ui_elements['active_profile_select'].set_options(backend.get_profile_names(), value=new_name)
                        
                        # Update the chart title with the new profile name
                        ui.run_javascript(f'updateChartTitle("{new_name}")')
                        
                        # Save to config file immediately
                        config_saved = save_to_config_file()
                        if config_saved:
                            ui.notify(f'Profile renamed to "{new_name}" and saved to config', type='positive')
                            logger.info(f"Renamed profile: {previous_name} -> {new_name} and saved to config")
                        else:
                            ui.notify(f'Profile renamed to "{new_name}" but failed to save config', type='warning')
                            logger.info(f"Renamed profile: {previous_name} -> {new_name} but failed to save config")
                        dialog.close()
                    elif not new_name:
                        ui.notify('Profile name cannot be empty. Please enter a valid name.', type='warning')
                    else:
                        # Name hasn't changed, just close dialog
                        dialog.close()
                
                ui.button('Save', on_click=save_profile_name).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                ui.button('Cancel', on_click=dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
        
        dialog.open()

    def edit_curve_name():
        with ui.dialog() as dialog, ui.card():
            ui.label('Edit Curve Name').classes('text-lg font-semibold mb-2')
            previous_name = selected_curve.name
            name_input = ui.input('Curve Name', value=selected_curve.name).classes('w-64')
            
            with ui.row():
                async def save_name():
                    nonlocal selected_profile, selected_curve
                    new_name = name_input.value.strip()
                    if new_name and new_name != previous_name:
                        # Check for duplicate curve names within the current profile
                        existing_curves = selected_profile.get_all_curves()
                        existing_names = [curve.name for curve in existing_curves.values()]
                        if new_name in existing_names:
                            ui.notify(f'Curve name "{new_name}" already exists in this profile. Please choose a different name.', type='warning')
                            return  # Don't close dialog, let user try again
                        
                        #active_profile.rename_curve(previous_name, new_name)
                        selected_curve.set_name(new_name)

                        # Update UI
                        ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=new_name)
                        await ui.run_javascript(f'updateCurveName("{previous_name}", "{new_name}")')
                        
                        # Save to config file immediately
                        config_saved = save_to_config_file()
                        if config_saved:
                            ui.notify(f'Curve renamed to "{new_name}" and saved to config', type='positive')
                            logger.info(f"Renamed curve: {previous_name} -> {new_name} and saved to config")
                        else:
                            ui.notify(f'Curve renamed to "{new_name}" but failed to save config', type='warning')
                            logger.info(f"Renamed curve: {previous_name} -> {new_name} but failed to save config")
                        
                        dialog.close()
                    elif not new_name:
                        ui.notify('Curve name cannot be empty. Please enter a valid name.', type='warning')
                    else:
                        # Name hasn't changed, just close dialog
                        dialog.close()
                
                ui.button('Save', on_click=save_name).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                ui.button('Cancel', on_click=dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
        
        dialog.open()

    # Main fan curve UI using page_layout.frame
    with page_layout.frame('Fan Curve'):
        with ui.element('div').classes('flex w-full justify-center'):
            with ui.column().classes('w-3/4').style('height: 100dvh;'):
                # Combined Profile and Curve management section
                with ui.element('div').classes('w-full m-0 p-4'):
                    # Profile management row
                    with ui.row().classes('w-full gap-2 items-center mb-3 no-wrap'):
                        active_profile_select = ui.select(
                            options=backend.get_profile_names(), 
                            value=selected_profile.get_name(), 
                            label="Select Profile"
                        ).classes('flex-grow ellipsis').on_value_change(handle_active_profile_change)
                        ui_elements['active_profile_select'] = active_profile_select
                        
                        async def manual_save():
                            """Manual save function with user feedback."""
                            success = await save_current_profile_data()
                            if success:
                                ui.notify(f'Saved changes to "{selected_profile.get_name()}"', type='positive')
                            else:
                                ui.notify('Failed to save profile changes', type='negative')
                        
                        ui.button('Add Profile', icon='add', on_click=add_profile).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        ui.button('Rename Profile', icon='edit', on_click=edit_profile_name).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        ui.button('Remove Profile', icon='delete', on_click=remove_profile).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        ui.button('Save Profile', icon='save', on_click=manual_save).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap text-[#ffdd00!important]').props('flat no-wrap')
                    # Curve management row
                    with ui.row().classes('w-full gap-2 items-center no-wrap'):
                        active_curve_select = ui.select(
                            options=backend.get_curve_names(selected_profile.id), 
                            value=selected_curve.name, 
                            label="Select Curve"    
                        ).classes('flex-grow ellipsis').on_value_change(handle_active_curve_change)
                        ui_elements['active_curve_select'] = active_curve_select
                        
                        temp_selection = ui.select(
                            options=get_available_sensors(),
                            value=format_sensor_display_name(selected_curve.sensor) if selected_curve.sensor else 'None',
                            label='Temperature Source'
                        ).classes('flex-grow ellipsis')
                        
                        # Store reference in ui_elements for cross-function access
                        ui_elements['temp_selection'] = temp_selection
                        # Set the change handler after the temp_selection is created
                        temp_selection.on_value_change(lambda e: handle_sensor_change(e.value))
                        
                        # Configure Drives button - only shown when a drive monitor is selected
                        def update_configure_drives_button():
                            """Update visibility of configure drives button based on current sensor."""
                            current_sensor = selected_curve.sensor if selected_curve.sensor else None
                            if current_sensor and current_sensor.startswith('Drives.'):
                                configure_drives_btn.set_visibility(True)
                            else:
                                configure_drives_btn.set_visibility(False)
                        
                        async def open_drive_config():
                            """Open drive configuration dialog for the current drive monitor."""
                            current_sensor = selected_curve.sensor if selected_curve.sensor else None
                            if current_sensor and current_sensor.startswith('Drives.'):
                                await open_drive_selection_dialog(existing_sensor=current_sensor)
                        
                        configure_drives_btn = ui.button('Configure Drives', icon='settings', on_click=open_drive_config).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        ui_elements['configure_drives_btn'] = configure_drives_btn
                        
                        # Initially set button visibility based on current sensor
                        update_configure_drives_button()
                        
                        add_curve_btn = ui.button('Add Curve', icon='add').classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        ui.button('Rename Curve', icon='edit', on_click=edit_curve_name).classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        remove_curve_btn = ui.button('Remove Curve', icon='delete').classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')
                        reset_btn = ui.button('Reset Curve', icon='refresh').classes('border-solid border-2 border-[#ffdd00] whitespace-nowrap').props('flat color="white" no-wrap')

                # Chart and curve points layout
                with ui.row().classes('no-wrap min-w-0 w-full h-full gap-0 mb-0'):
                    # Chart container
                    with ui.card().classes('flex-1 min-w-0 self-stretch mb-4').style('min-height: 550px;'):
                        ui.html('<canvas id="fanCurveChart" style="width: 100%; height: 100%;"></canvas>').classes('w-full h-full')
                    
                    # Active curve points panel - positioned to the right of chart
                    with ui.card().classes('ml-4 p-4'):
                        current_info = ui.html('<div id="current-info" class="text-sm max-h-96 overflow-y-auto"></div>')
                        
                        # Temperature display section
                        ui.separator().classes('my-4')
                        ui.label('Temperature Sensors').classes('text-sm font-semibold mb-2')
                        
                        # Create a container for the temperature display that can be refreshed
                        temp_display_container = ui.column().classes('w-full')
                        
                        # Function to refresh the temperature display
                        def refresh_temperature_display():
                            """Refresh the temperature sensors display section."""
                            # Clear the container
                            temp_display_container.clear()
                            
                            # Get the temperature backend reference
                            temp_backend = globals.temp_sensor_service
                            
                            with temp_display_container:
                                if temp_backend:
                                    # Display only sensors with actual hardware available
                                    all_sensors_flat = temp_backend.get_all_sensors_flat()
                                    
                                    # Filter for only sensors with hardware available
                                    # This prevents showing sensors from saved config that no longer have hardware
                                    available_sensors = {}
                                    for sensor_name, sensor_obj in all_sensors_flat.items():
                                        if sensor_obj.enabled and sensor_obj.is_hardware_available():
                                            available_sensors[sensor_name] = sensor_obj
                                    
                                    # Also get drive monitors
                                    drive_monitors = temp_backend.get_all_drive_monitors()
                                    available_drive_monitors = {}
                                    for monitor_name, monitor_obj in drive_monitors.items():
                                        if monitor_obj.enabled and monitor_obj.is_hardware_available():
                                            available_drive_monitors[f"Drives.{monitor_name}"] = monitor_obj
                                    
                                    if available_sensors or available_drive_monitors:
                                        with ui.column().classes('w-full gap-1'):
                                            # Display regular sensors
                                            for sensor_name, sensor_obj in available_sensors.items():
                                                with ui.row().classes('w-full justify-between items-center text-xs'):
                                                    # Sensor name
                                                    ui.label(sensor_name).classes('flex-grow text-left truncate')
                                                    
                                                    # Temperature display bound to sensor
                                                    temp_label = ui.label().classes('text-right font-mono')
                                                    
                                                    # Bind the temperature display to the sensor's temperature attribute
                                                    temp_label.bind_text_from(sensor_obj, 'temperature', lambda temp: f"{temp:.1f}°C" if temp > 0 else "N/A")
                                            
                                            # Display drive monitors
                                            for monitor_name, monitor_obj in available_drive_monitors.items():
                                                with ui.row().classes('w-full justify-between items-center text-xs'):
                                                    # Monitor name and status
                                                    monitor_curve = backend.get_curve(monitor_obj.curve_id)
                                                    monitor_display_name = f"{monitor_obj.name} ({monitor_curve.name})"
                                                    ui.label(monitor_display_name).classes('flex-grow text-left truncate')

                                                    # Temperature display bound to drive monitor
                                                    temp_label = ui.label().classes('text-right font-mono')
                                                    
                                                    # Bind the temperature display to the monitor's current_temperature attribute
                                                    temp_label.bind_text_from(monitor_obj, 'current_temperature', lambda temp: f"{temp:.1f}°C" if temp > 0 else "N/A")
                                    else:
                                        ui.label('No hardware sensors detected').classes('text-xs text-gray-500')
                                else:
                                    ui.label('Temperature backend not initialized').classes('text-xs text-red-500')
                        
                        # Initial population of temperature display
                        refresh_temperature_display()
                        
                        # Store reference to refresh function for use in apply_drive_selection
                        ui_elements['refresh_temp_display'] = refresh_temperature_display

            # Enhanced JavaScript implementation with dynamic curve support
            chart_script = f"""
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-dragdata@latest/dist/chartjs-plugin-dragdata.min.js"></script>
            """
            ui.add_body_html(chart_script)
            
            app.add_static_files('/js', 'js') 
            ui.add_head_html('<script type="text/javascript" src="/js/chart_script.js"></script>')
            
            # Also include the F-shape CSS from overview page for drive buttons
            app.add_static_files('/css', 'css')
            ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/f-shape.css">')
            ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/f-shape-rotated.css">')
            ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/drive-selection.css">')
            ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/number-input.css">')
            
            # Add navigation protection JavaScript
            ui.add_head_html('<script type="text/javascript" src="/js/navigation_protection.js"></script>')

            # Function to update curve controls dynamically
            async def update_curve_controls():
                # Load the initial profile data into the chart
                await load_profile_data_to_chart(selected_profile)
                
                # Set the first curve as active in JavaScript
                first_curve_obj = next(iter(selected_profile.get_all_curves().values()))
                await ui.run_javascript(f'setActiveCurve("{first_curve_obj.name}")')
                logger.info(f"Initialized with profile: {selected_profile.get_name()}")
                
                # Reset unsaved changes flag after initialization is complete
                # Add a small delay to ensure chart is fully synchronized
                await asyncio.sleep(0.5)
                await reset_all_flags()

            # Function to handle sending data to Python
            async def send_data_to_python():
                try:
                    data_json = await ui.run_javascript('getCurrentDataForPython()', timeout=5.0)
                    if data_json:
                        data = json.loads(data_json)
                        process_fan_curves_data(
                            data['curves'], 
                            data['activeCurve'],
                            data.get('visibility', {})
                        )
                        ui.notify('Multi-curve data sent to Python successfully! Check terminal.', type='positive')
                    else:
                        ui.notify('No data received from JavaScript.', type='warning')
                except Exception as e:
                    logger.info(f"Error sending curve data to Python: {e}")
                    ui.notify('Error sending data to Python.', type='negative')

            # Function for the "Apply" button
            async def show_apply_summary():
                data_json = await ui.run_javascript('getCurrentDataForPython()', timeout=5.0)
                if not data_json: return
                
                data = json.loads(data_json)
                fan_curves = data['curves']
                active_curve_key = data['activeCurve']
                
                message_html = f'<p class="text-lg">{len(fan_curves)} fan curves applied!</p>'
                message_html += '<p class="font-bold mt-4">Configuration:</p><ul class="list-disc list-inside">'
                
                for curve_info in fan_curves.values():
                    points = curve_info['data']
                    if not points: continue
                    range_str = f"{points[0]['x']}°C to {points[-1]['x']}°C"
                    sensor = curve_info.get('sensor', 'N/A')
                    message_html += f"<li><b>{curve_info['name']}</b> (Sensor: {sensor}): {len(points)} points ({range_str})</li>"
                
                message_html += '</ul>'
                message_html += f'<p class="mt-4"><b>Active:</b> {fan_curves[active_curve_key]["name"]}</p>'
                message_html += '<p class="mt-4 text-sm text-gray-500">In a real system, these settings would be sent to the fan controllers.</p>'

                with ui.dialog() as dialog, ui.card():
                    ui.html(message_html)
                    with ui.row().classes('w-full justify-end'):
                        ui.button('Close', on_click=dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                dialog.open()

            # Helper function to check if user has seen the multi-curve dialog
            def should_show_multi_curve_dialog():
                """Check if the user should see the multi-curve explanation dialog."""
                try:
                    import globals
                    if globals.layoutState:
                        # Check the options in the chassis configuration
                        return not getattr(globals.layoutState, 'hide_multi_curve_dialog', False)
                    return True  # Show dialog if layoutState not available
                except Exception as e:
                    logger.info(f"Error reading chassis preferences: {e}")
                    return True  # Show dialog on error

            def save_multi_curve_dialog_preference(hide_dialog):
                """Save the user's preference for showing the multi-curve dialog."""
                try:
                    import globals
                    if globals.layoutState:
                        # Save the preference in the chassis state
                        globals.layoutState.hide_multi_curve_dialog = hide_dialog
                        globals.layoutState.save_config()
                        logger.info(f"Saved multi-curve dialog preference: {hide_dialog}")
                except Exception as e:
                    logger.info(f"Error saving chassis preferences: {e}")

            # Helper function to check if user has seen the drive selection dialog
            def should_show_drive_selection_dialog():
                """Check if the user should see the drive selection explanation dialog."""
                try:
                    import globals
                    if globals.layoutState:
                        # Check the options in the chassis configuration
                        return not getattr(globals.layoutState, 'hide_drive_selection_dialog', False)
                    return True  # Show dialog if layoutState not available
                except Exception as e:
                    logger.info(f"Error reading chassis preferences: {e}")
                    return True  # Show dialog on error

            def save_drive_selection_dialog_preference(hide_dialog):
                """Save the user's preference for showing the drive selection dialog."""
                try:
                    import globals
                    if globals.layoutState:
                        # Save the preference in the chassis state
                        globals.layoutState.hide_drive_selection_dialog = hide_dialog
                        globals.layoutState.save_config()
                        logger.info(f"Saved drive selection dialog preference: {hide_dialog}")
                except Exception as e:
                    logger.info(f"Error saving chassis preferences: {e}")

            # Connect button events with async callbacks
            async def handle_add_curve():
                nonlocal selected_profile, selected_curve
                
                # Check if this is the first time adding a second curve and user hasn't disabled the dialog
                current_curve_count = len(selected_profile.get_all_curves())
                
                # Show explanation dialog only when adding the second curve (first additional curve)
                if current_curve_count == 1 and should_show_multi_curve_dialog():
                    # Show multi-curve explanation dialog
                    with ui.dialog() as info_dialog, ui.card().classes('p-6 max-w-2xl'):
                        ui.html('<h3 class="text-xl font-semibold mb-4">Multiple Temperature Curves</h3>')
                        
                        ui.html('''
                        <div class="space-y-4 text-sm">
                            <p class="text-base">
                                <strong>You're adding a second curve to this fan profile!</strong> 
                                This allows you to control fans based on multiple temperature sources.
                            </p>
                            
                            <div class="bg-blue-50 border-l-4 border-blue-400 p-4 rounded">
                                <h4 class="font-semibold text-blue-800 mb-2">How Multi-Curve Control Works:</h4>
                                <ul class="list-disc list-inside space-y-1 text-blue-700">
                                    <li><strong>Maximum Speed Rule:</strong> The fan will run at the highest speed demanded by any curve</li>
                                    <li><strong>Multi Response:</strong> If any sensor is reading high, fans ramp up to cool the system</li>
                                    <li><strong>Hot Spot Protection:</strong> Each curve can monitor different components (CPU, GPU, drives, etc.)</li>
                                </ul>
                            </div>
                            
                            <div class="bg-green-50 border-l-4 border-green-400 p-4 rounded">
                                <h4 class="font-semibold text-green-800 mb-2">Example Scenario:</h4>
                                <p class="text-green-700">
                                    <strong>Curve 1:</strong> CPU at 45°C → wants 60% fan speed<br>
                                    <strong>Curve 2:</strong> GPU at 75°C → wants 85% fan speed<br>
                                    <strong>Result:</strong> Fans run at 85% to cool the hottest component
                                </p>
                            </div>
                        
                        </div>
                        ''')
                        
                        # Checkbox to not show again
                        hide_dialog_checkbox = ui.checkbox('Don\'t show this explanation again', value=False).classes('mt-4')
                        
                        with ui.row().classes('w-full justify-end gap-2 mt-6'):
                            async def proceed_with_add():
                                # Save preference if checkbox is checked
                                if hide_dialog_checkbox.value:
                                    save_multi_curve_dialog_preference(True)
                                
                                info_dialog.close()
                                
                                # Now actually add the curve
                                await add_curve_logic()
                            
                            ui.button('Got it, Add Curve', icon='add', on_click=proceed_with_add).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                            ui.button('Cancel', on_click=info_dialog.close).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    
                    info_dialog.open()
                else:
                    # No dialog needed, add curve directly
                    await add_curve_logic()

            async def add_curve_logic():
                """The actual logic for adding a curve."""
                nonlocal selected_profile, selected_curve
                
                new_curve_id = selected_profile.add_curve()
                selected_curve = selected_profile.get_curve(new_curve_id)  # Update active curve locally
                
                # Set temperature source to None for new curves
                selected_curve.sensor = None
                # Set sensor in profile's curve collection (use curve ID)
                selected_profile.set_curve_sensor(new_curve_id, None)
                logger.info(f"New curve '{selected_curve.name}' created with no temperature source assigned")
                
                # Mark as having unsaved structural changes
                await set_unsaved_changes(True)
                
                # Update UI
                ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=selected_curve.name)
                if ui_elements.get('temp_selection'):
                    # Refresh temperature selection options for the new curve
                    updated_options = get_available_sensors()
                    sensor_display_name = 'None'
                    safe_set_temp_selection_options(updated_options, sensor_display_name)
                
                # Update configure drives button visibility for new curve
                if ui_elements.get('configure_drives_btn'):
                    # New curves have no sensor assigned, so hide the configure drives button
                    ui_elements['configure_drives_btn'].set_visibility(False)
                
                # Update JavaScript
                sensor_for_js = ""
                await ui.run_javascript(f'addNewCurve("{selected_curve.name}", "{sensor_for_js}")')
                
                ui.notify(f'Curve "{selected_curve.name}" created (unsaved)', type='info')
                logger.info(f"Added new curve: {selected_curve.name} with sensor: None (unsaved)")
                
            async def handle_remove_curve():
                nonlocal selected_profile, selected_curve
                
                if len(selected_profile.get_all_curves()) > 1:
                    old_curve_name = selected_curve.name
                    
                    # Show confirmation dialog
                    with ui.dialog() as dialog, ui.card().classes('p-6'):
                        ui.html(f'<h3 class="text-lg font-semibold mb-4">Delete Curve?</h3>')
                        ui.html(f'<p class="mb-4">Are you sure you want to permanently delete the curve <strong>"{old_curve_name}"</strong>?</p>')

                        with ui.row().classes('w-full justify-end gap-2'):
                            async def confirm_delete():
                                nonlocal selected_profile, selected_curve
                                
                                # Get the curve ID from the name, then remove the curve from the profile
                                old_curve_id = backend.get_curve_id_by_name(selected_profile.id, old_curve_name)
                                if old_curve_id:
                                    # Remove any associated drive monitors before removing the curve
                                    temp_backend = globals.temp_sensor_service
                                    if temp_backend:
                                        removed_count = temp_backend.remove_drive_monitors_for_curve(old_curve_id)
                                        if removed_count > 0:
                                            logger.info(f"Removed {removed_count} drive monitor(s) associated with curve: {old_curve_name}")
                                            # Save the temperature backend configuration after removing drive monitors
                                            temp_config_saved = temp_backend.save_configuration()
                                            if temp_config_saved:
                                                logger.info(f"Temperature backend configuration saved after removing drive monitors")
                                            else:
                                                logger.warning(f" Failed to save temperature backend configuration")
                                    
                                    selected_profile.remove_curve(old_curve_id)
                                
                                # Switch to the first remaining curve
                                next_curve_obj = next(iter(selected_profile.get_all_curves().values()))
                                selected_curve = next_curve_obj
                                
                                # Update UI elements
                                ui_elements['active_curve_select'].set_options(backend.get_curve_names(selected_profile.id), value=next_curve_obj.name)
                                if ui_elements.get('temp_selection'):
                                    sensor_value = selected_curve.sensor if selected_curve.sensor else None
                                    sensor_display_name = format_sensor_display_name(sensor_value) if sensor_value else 'None'
                                    # Refresh temperature selection options for the new active curve
                                    updated_options = get_available_sensors()
                                    safe_set_temp_selection_options(updated_options, sensor_display_name)
                                
                                # Update configure drives button visibility based on new curve's sensor
                                if ui_elements.get('configure_drives_btn'):
                                    current_sensor = selected_curve.sensor if selected_curve.sensor else None
                                    if current_sensor and current_sensor.startswith('Drives.'):
                                        ui_elements['configure_drives_btn'].set_visibility(True)
                                    else:
                                        ui_elements['configure_drives_btn'].set_visibility(False)
                                
                                # Refresh the temperature display to remove any deleted drive monitors
                                if ui_elements.get('refresh_temp_display'):
                                    ui_elements['refresh_temp_display']()
                                
                                # Update JavaScript chart
                                await ui.run_javascript('removeActiveCurve()')
                                
                                # Mark as having unsaved structural changes
                                await set_unsaved_changes(True)
                                
                                ui.notify(f'Curve "{old_curve_name}" deleted (unsaved)', type='info')
                                logger.info(f"Removed curve: {old_curve_name} (unsaved)")
                                
                                dialog.close()
                            
                            def cancel_delete():
                                dialog.close()
                            
                            ui.button('Delete', on_click=confirm_delete).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                            ui.button('Cancel', on_click=cancel_delete).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    
                    dialog.open()
                else:
                    ui.notify('Cannot remove the last curve in a profile', type='warning')
            
            add_curve_btn.on('click', handle_add_curve)
            remove_curve_btn.on('click', handle_remove_curve)
            reset_btn.on('click', lambda: ui.run_javascript('resetActiveCurve()'))
            
            # Initialize controls when the page is ready for the client
            ui.on('fan_curve_ready', update_curve_controls)
            
            # Register handler for JavaScript to check unsaved changes status
            ui.on('check_unsaved_changes', check_and_update_unsaved_changes)