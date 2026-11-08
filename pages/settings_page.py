import json
import time

from nicegui import ui, run
from authentication import require_auth
import globals
import page_layout
from api_key_manager import api_key_manager

@require_auth
def settingsPage():
    """Settings page for chassis layout and fan configuration."""

    # Use a mutable object to store the flag so it can be accessed in nested functions
    state_flags = {'ignoring_change': False}

    # Store UI element references
    ui_refs = {'model_switch': None, 'sn_switch': None}

    # Ensure at least one switch is on during initialization
    if not globals.layoutState.get_model_display() and not globals.layoutState.get_sn_display():
        # If both are off, turn on model display by default
        globals.layoutState.set_model_display(True)

    def change_product(new_product):
        """Change the chassis product and reset layout."""
        globals.layoutState.reset_chassis()
        globals.layoutState.set_product(new_product)
        wattage_source_ui.refresh()

    def change_model_display(value):
        # If turning off model display, ensure SN display is on
        if not value and not globals.layoutState.get_sn_display():
            globals.layoutState.set_sn_display(True)
            # Update the SN switch UI
            if ui_refs['sn_switch']:
                ui_refs['sn_switch'].set_value(True)
        globals.layoutState.set_model_display(value)

    def change_sn_display(value):
        # If turning off SN display, ensure model display is on
        if not value and not globals.layoutState.get_model_display():
            globals.layoutState.set_model_display(True)
            # Update the model switch UI
            if ui_refs['model_switch']:
                ui_refs['model_switch'].set_value(True)
        globals.layoutState.set_sn_display(value)

    def handle_product_change(e):
        """Handle product selection change."""
        # Ignore programmatic changes
        if state_flags['ignoring_change']:
            return

        current_product = globals.layoutState.get_product()
        new_product = e.value

        # Only show dialog if actually changing to a different product
        if new_product != current_product:
            reset_dialog(new_product)

    def reset_dialog(new_product):
        """Show confirmation dialog when changing chassis layout."""
        def on_no():
            # Set flag to ignore the change event when resetting value
            state_flags['ignoring_change'] = True
            product_select.set_value(globals.layoutState.get_product())
            state_flags['ignoring_change'] = False
            dialog.close()

        with ui.dialog().props('persistent') as dialog, ui.card():
            ui.label('Changing layouts will reset backplanes and drives. Continue?')
            with ui.row().classes('w-full justify-center'):
                ui.button('Yes', on_click=lambda: (change_product(new_product), dialog.close())).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                ui.button('No', on_click=on_no).classes('border-solid border-2 border-[#ffdd00]').props('flat color="white"')
        dialog.open()

    def create_pwm_settings():
        """Create PWM settings interface. Shows all 3 headers per connected powerboard;
        headers without a fan wall assignment are displayed but disabled."""
        if not globals.powerboardDict:
            ui.label('No powerboards detected for PWM settings.').classes('text-gray-500 italic')
            return

        svc = globals.fan_control_service

        # Build lookup: (pb_location, header_index) -> FanWall
        wall_by_assignment: dict = {}
        if svc and svc.fan_walls:
            for wall in svc.fan_walls.values():
                if wall.powerboard_id is not None and wall.header_index is not None:
                    wall_by_assignment[(wall.powerboard_id, wall.header_index)] = wall

        header_labels = {0: 'Row 1', 1: 'Row 2', 2: 'Row 3'}
        # (pb_location, header_index) -> slider; keyed this way so apply can rebuild per-PB arrays
        pwm_inputs: dict = {}
        pb_objects: dict = {}  # pb_location -> pb_obj, for use in apply

        async def apply_pwm_settings():
            """Write all slider values to each powerboard's EEPROM."""
            try:
                pb_speeds: dict = {}
                for (pb_location, header_index), slider in pwm_inputs.items():
                    pb_obj = pb_objects.get(pb_location)
                    if pb_obj is None:
                        continue
                    if pb_location not in pb_speeds:
                        pb_speeds[pb_location] = {'pb': pb_obj, 'speeds': list(pb_obj.get_saved_fan_pwm())}
                    pb_speeds[pb_location]['speeds'][header_index] = int(slider.value)

                for pb_location, entry in pb_speeds.items():
                    speeds = entry['speeds']
                    await run.io_bound(entry['pb'].set_fan_speed, *speeds)
                    entry['pb'].set_running_fan_pwm(*speeds)
                    for header_index, speed in enumerate(speeds):
                        wall = wall_by_assignment.get((pb_location, header_index))
                        if wall is not None:
                            wall.current_speed = speed

                if svc is not None:
                    svc.save_config_immediate()

                ui.notify("PWM settings applied successfully!",
                         position='bottom-right', type='positive', group=False)

            except Exception as e:
                ui.notify(f"Error applying PWM settings: {str(e)}",
                         position='bottom-right', type='negative', group=False)

        with ui.column().classes('w-full gap-4'):
            for pb_id in sorted(globals.powerboardDict.keys()):
                pb_obj = globals.powerboardDict[pb_id]
                saved = pb_obj.get_saved_fan_pwm()
                pb_objects[pb_obj.location] = pb_obj

                with ui.card().classes('w-full'):
                    ui.label(f'Powerboard {pb_obj.location}').classes('text-lg font-semibold mb-2')
                    with ui.grid(columns=3).classes('gap-4 w-full'):
                        for header_index in range(3):
                            wall = wall_by_assignment.get((pb_obj.location, header_index))
                            assigned = wall is not None

                            with ui.column().classes('items-center gap-2'):
                                if assigned:
                                    ui.label(wall.name)
                                else:
                                    ui.label(f'{header_labels[header_index]} - unassigned').classes('text-gray-500 italic text-sm')

                                slider = ui.slider(
                                    min=0, max=100, step=1,
                                    value=int(saved[header_index])
                                ).classes('w-32')
                                if not assigned:
                                    slider.props('disable')
                                    slider.tooltip('No fan wall assigned to this header')

                                ui.label().bind_text_from(
                                    slider, 'value', lambda v: f'{int(v)}%'
                                ).classes('' if assigned else 'text-gray-500')

                                pwm_inputs[(pb_obj.location, header_index)] = slider

            # Apply button
            with ui.row().classes('justify-center w-full mt-4'):
                ui.button(
                    'Apply PWM Settings',
                    on_click=apply_pwm_settings
                ).classes('border-solid border-2 border-[#ffdd00] text-white px-6 py-2').props('flat')

    def create_fan_wall_assignments():
        """Create fan wall assignment UI."""
        if not globals.fan_control_service or not globals.fan_control_service.fan_walls:
            return ui.label('No fan walls initialized.').classes('text-gray-500 italic')

        svc = globals.fan_control_service
        if svc.config_load_error:
            ui.label(svc.config_load_error).classes('text-red-500 text-sm mb-2')
        pb_options = {pb.location: f'Powerboard {pb.location}' for pb in sorted(globals.powerboardDict.values(), key=lambda p: p.location)}
        for wall in svc.fan_walls.values():
            if wall.powerboard_id is not None and wall.powerboard_id not in pb_options:
                # A configured board that is offline must stay in the options or ui.select raises on a value not in options.
                pb_options[wall.powerboard_id] = f'Powerboard {wall.powerboard_id} (disconnected)'
        header_options = {0: 'Row 1', 1: 'Row 2', 2: 'Row 3'}

        with ui.grid(columns=3).classes('w-full gap-x-4 gap-y-2'):
            # Column headers
            ui.label('Fan Wall').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')
            ui.label('Powerboard').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')
            ui.label('Header').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')

            for wall_id in sorted(svc.fan_walls.keys()):
                wall = svc.fan_walls[wall_id]

                ui.label(wall.name).classes('text-sm self-center')

                pb_select = ui.select(
                    options={None: 'Unassigned', **pb_options},
                    value=wall.powerboard_id,
                ).classes('w-full')

                header_select = ui.select(
                    options={None: 'Unassigned', **header_options},
                    value=wall.header_index,
                ).classes('w-full')

                # Returns early when the selected pair equals the stored pair, so programmatic select updates do not fire a spurious success toast.
                def on_change(_, wid=wall_id, pb_sel=pb_select, hdr_sel=header_select):
                    stored = svc.fan_walls[wid]
                    if (pb_sel.value, hdr_sel.value) == (stored.powerboard_id, stored.header_index):
                        return
                    success = svc.set_wall_assignment(wid, pb_sel.value, hdr_sel.value)
                    if not success:
                        # Revert selects to current (unchanged) values
                        w = svc.fan_walls[wid]
                        pb_sel.set_value(w.powerboard_id)
                        hdr_sel.set_value(w.header_index)
                        ui.notify(
                            'That powerboard/header is already assigned to another fan wall.',
                            position='bottom-right', type='warning', group=False
                        )
                    else:
                        ui.notify(
                            f'{svc.fan_walls[wid].name} assignment saved.',
                            position='bottom-right', type='positive', group=False
                        )

                pb_select.on_value_change(on_change)
                header_select.on_value_change(on_change)

    @ui.refreshable
    def wattage_source_ui():
        create_wattage_source_assignments()

    def create_wattage_source_assignments():
        """Configure independent wattage source (pdb + connector) per wall display row."""
        if not globals.fan_control_service or not globals.fan_control_service.fan_walls:
            return ui.label('No fan walls initialized.').classes('text-gray-500 italic')

        svc = globals.fan_control_service
        pb_options = {pb.location: f'Powerboard {pb.location}' for pb in sorted(globals.powerboardDict.values(), key=lambda p: p.location)}
        connector_options = {
            'watt_sec_1_2': 'Section 1-2',
            'watt_sec_3_4': 'Section 3-4',
        }

        _chassis_watt_rows = {'Hako-Core': 3, 'Hako-Core DAS': 4, 'Hako-Core Mini': 2, 'HF-L1': 1}
        chassis = globals.layoutState.get_product()
        watt_wall_ids = list(range(1, _chassis_watt_rows.get(chassis, 3) + 1))

        with ui.grid(columns=3).classes('w-full gap-x-4 gap-y-2'):
            ui.label('Row').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')
            ui.label('Powerboard').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')
            ui.label('Connector').classes('text-xs text-gray-400 uppercase tracking-wide font-semibold')

            for wall_id in watt_wall_ids:
                wall = svc.fan_walls.get(wall_id)
                if not wall:
                    continue

                ui.label(f'Drive Row {wall_id}').classes('text-sm self-center')

                pb_sel = ui.select(
                    options={None: 'Auto', **pb_options},
                    value=wall.watt_powerboard_id,
                ).classes('w-full')

                conn_sel = ui.select(
                    options={None: 'Auto', **connector_options},
                    value=wall.watt_attr,
                ).classes('w-full')

                def on_watt_change(_, wid=wall_id, pb_s=pb_sel, conn_s=conn_sel):
                    svc.set_wall_wattage_source(wid, pb_s.value, conn_s.value)
                    ui.notify(
                        f'{svc.fan_walls[wid].name} wattage source saved.',
                        position='bottom-right', type='positive', group=False
                    )

                pb_sel.on_value_change(on_watt_change)
                conn_sel.on_value_change(on_watt_change)

    def create_api_keys_section():
        """Create API key management UI."""
        state = {'pending_delete_id': None, 'generated_key': ''}

        @ui.refreshable
        def api_keys_list():
            keys = api_key_manager.list_keys()
            if not keys:
                ui.label('No API keys configured.').classes('text-gray-500 italic text-sm')
                return
            with ui.column().classes('w-full gap-2'):
                for key in keys:
                    created = time.strftime('%Y-%m-%d', time.localtime(key['created_at']))
                    with ui.row().classes('w-full items-center justify-between'):
                        with ui.column().classes('gap-0'):
                            ui.label(key['name']).classes('font-medium text-sm')
                            ui.label(f"{key['prefix']}...  •  Created {created}").classes('text-xs text-gray-500 font-mono')
                        ui.button(
                            icon='delete',
                            on_click=lambda _, kid=key['id'], kname=key['name']: open_delete_dialog(kid, kname)
                        ).props('flat color=red dense')

        with ui.dialog().props('persistent') as create_dialog, ui.card().classes('p-6 min-w-80'):
            ui.label('Generate New API Key').classes('text-lg font-bold mb-4')
            name_input = ui.input('Key Name', placeholder='e.g. Home Assistant').classes('w-full mb-2')
            create_error = ui.label('').classes('text-red-500 text-sm mb-2')
            create_error.visible = False

            def do_create():
                name = name_input.value.strip()
                if not name:
                    create_error.text = 'Key name is required'
                    create_error.visible = True
                    return
                try:
                    state['generated_key'] = api_key_manager.create_key(name)
                except Exception as e:
                    create_error.text = f'Could not save API key: {e}'
                    create_error.visible = True
                    return
                name_input.value = ''
                create_error.visible = False
                create_dialog.close()
                key_display_label.set_text(state['generated_key'])
                show_key_dialog.open()
                api_keys_list.refresh()

            def cancel_create():
                name_input.value = ''
                create_error.visible = False
                create_dialog.close()

            with ui.row().classes('w-full justify-end gap-2 mt-2'):
                ui.button('Cancel', on_click=cancel_create).props('flat color=white')
                ui.button('Generate', on_click=do_create).classes('border-solid border-2 border-[#ffdd00]').props('flat color=white')

        with ui.dialog().props('persistent') as show_key_dialog, ui.card().classes('p-6 min-w-96'):
            ui.label('API Key Generated').classes('text-lg font-bold mb-2')
            ui.label('Copy this key now — it will not be shown again.').classes('text-sm text-amber-400 mb-4')
            key_display_label = ui.label('').classes('font-mono text-sm bg-gray-800 p-3 rounded w-full break-all select-all')

            async def copy_key():
                await ui.run_javascript(f'navigator.clipboard.writeText({json.dumps(state["generated_key"])})')
                ui.notify('Key copied to clipboard', position='bottom-right', type='positive', group=False)

            def close_key_dialog():
                # Clear the raw key from memory and the label once the dialog is dismissed; only its hash is stored.
                state['generated_key'] = ''
                key_display_label.set_text('')
                show_key_dialog.close()

            with ui.row().classes('w-full justify-end gap-2 mt-4'):
                ui.button('Copy', icon='content_copy', on_click=copy_key).classes('border-solid border-2 border-[#ffdd00]').props('flat color=white')
                ui.button('Done', on_click=close_key_dialog).props('flat color=white')

        with ui.dialog().props('persistent') as delete_dialog, ui.card().classes('p-6'):
            ui.label('Delete API Key?').classes('text-lg font-bold mb-2')
            delete_name_label = ui.label('').classes('text-sm text-gray-400 mb-2')
            ui.label('This immediately invalidates the key.').classes('text-sm text-gray-500 mb-4')

            def do_delete():
                if state['pending_delete_id']:
                    try:
                        api_key_manager.delete_key(state['pending_delete_id'])
                    except Exception as e:
                        ui.notify(f'Could not delete API key: {e}', position='bottom-right', type='negative', group=False)
                        return
                    state['pending_delete_id'] = None
                    delete_dialog.close()
                    api_keys_list.refresh()
                    ui.notify('API key deleted', position='bottom-right', type='positive', group=False)

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('Cancel', on_click=delete_dialog.close).props('flat color=white')
                ui.button('Delete', on_click=do_delete).classes('border-solid border-2 border-red-500').props('flat color=red')

        def open_delete_dialog(key_id: str, key_name: str):
            state['pending_delete_id'] = key_id
            delete_name_label.set_text(f'Key: {key_name}')
            delete_dialog.open()

        api_keys_list()
        with ui.row().classes('w-full mt-4'):
            ui.button('Generate New API Key', icon='add', on_click=create_dialog.open).classes('border-solid border-2 border-[#ffdd00]').props('flat color=white')

    # Main settings UI
    with page_layout.frame('Settings'):
        with ui.element('div').classes('flex w-full').style('justify-content: safe center;'):
            with ui.card():
                # Chassis Layout Section
                ui.label('Chassis Configuration').classes('text-xl font-bold mb-4')
                with ui.grid(columns=2).classes('gap-0 w-full').style('grid-auto-rows: 1fr;'):
                    ui.label('Chassis Layout:').classes('flex justify-start items-center')
                    product_select = ui.select(
                        ['Hako-Core', 'Hako-Core DAS', 'Hako-Core Mini', 'HF-L1'],
                        value=globals.layoutState.get_product(),
                        on_change=handle_product_change
                    )

                    ui.label('Show drive model:').classes('flex justify-start items-center ')
                    ui_refs['model_switch'] = ui.switch(value=globals.layoutState.get_model_display(), on_change=lambda e: change_model_display(e.value)).style('justify-content:end;')

                    ui.label('Show drive serial #:').classes('flex justify-start items-center')
                    ui_refs['sn_switch'] = ui.switch(value=globals.layoutState.get_sn_display(), on_change=lambda e: change_sn_display(e.value)).style('justify-content:end;')

                    ui.label('Invert chassis orientation:').classes('flex justify-start items-center')
                    orientation_switch = ui.switch(
                        value=globals.layoutState.chassis_is_inverted(),
                        on_change=lambda e: globals.layoutState.set_chassis_inverted(e.value)
                    ).style('justify-content:end;')
                    orientation_switch.tooltip('Toggle if your chassis is physically mounted inverted')

                    ui.label('Temperature Units:').classes('flex justify-start items-center')
                    # Map display names to backend values
                    unit_options = {'Celsius (C°)': 'C', 'Fahrenheit (F°)': 'F'}
                    current_unit = globals.layoutState.get_units()
                    # Find the display name for the current value
                    current_display = next((k for k, v in unit_options.items() if v == current_unit), 'Celsius (C°)')

                    ui.select(
                        list(unit_options.keys()),
                        value=current_display,
                        on_change=lambda e: globals.layoutState.set_units(unit_options[e.value])
                    ).style('justify-content:end;')

                ui.separator().classes('my-4')

                # Clear All Backplanes Section
                with ui.row().classes('w-full justify-center'):
                    def clear_all_backplanes():
                        """Clear all backplanes with confirmation dialog."""
                        def on_confirm():
                            globals.layoutState.clear_all_backplanes()
                            ui.notify("All backplanes cleared successfully!",
                                     position='bottom-right', type='positive', group=False)
                            confirm_dialog.close()

                        def on_cancel():
                            confirm_dialog.close()

                        with ui.dialog().props('persistent') as confirm_dialog, ui.card().classes('p-6'):
                            ui.label('Clear All Backplanes?').classes('text-xl font-bold mb-4')
                            ui.label('This will remove all backplanes and their drive assignments. This action cannot be undone.').classes('text-sm text-gray-400 mb-4')
                            with ui.row().classes('w-full justify-center gap-4'):
                                ui.button('Yes, Clear All', on_click=on_confirm).classes('border-solid border-2 border-red-500 text-red-500 px-6 py-2').props('flat')
                                ui.button('Cancel', on_click=on_cancel).classes('border-solid border-2 border-[#ffdd00] text-white px-6 py-2').props('flat')
                        confirm_dialog.open()

                    ui.button(
                        'Clear All Backplanes',
                        on_click=clear_all_backplanes,
                        icon='delete_sweep'
                    ).classes('bg-red-500 text-white px-6 py-2').props('flat')
                    ui.label('Remove all backplanes and drive assignments').classes('text-xs text-gray-500 ml-2 self-center')

                ui.separator().classes('mb-6')

                # PWM Settings Section
                with ui.column().classes('w-full'):
                    ui.label('Default Fan Speed').classes('text-xl font-bold mb-4')
                    ui.label('These will be used when the system starts and persist between power cycles.').classes('text-sm text-gray-500 mb-2')
                    create_pwm_settings()

                ui.separator().classes('mb-6')

                # Fan Wall Assignments Section
                with ui.column().classes('w-full'):
                    ui.label('Fan Wall Assignments').classes('text-xl font-bold mb-2')
                    ui.label('Assign each fan wall to a powerboard and header row. Each combination can only be used once.').classes('text-sm text-gray-500 mb-4')
                    create_fan_wall_assignments()

                ui.separator().classes('mb-6')

                # Wattage Source Assignments Section
                with ui.column().classes('w-full'):
                    ui.label('Wattage Source Assignments').classes('text-xl font-bold mb-2')
                    ui.label('Override which powerboard connector supplies power readings for each display row. Defaults to the fan wall assignment if not set.').classes('text-sm text-gray-500 mb-4')
                    wattage_source_ui()

                ui.separator().classes('mb-6')

                # API Keys Section
                with ui.column().classes('w-full'):
                    ui.label('API Keys').classes('text-xl font-bold mb-2')
                    ui.label('Keys are stored as hashes in config/api_config.json. Each key is shown only once at creation.').classes('text-sm text-gray-500 mb-4')
                    create_api_keys_section()

                # Additional spacing
                ui.space().classes('h-2')
