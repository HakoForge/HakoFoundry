"""
Powerboards Page

Displays live telemetry for each connected powerboard: fan header RPM and PWM,
per-section and per-shunt wattage, board identity, and control mode.
"""
import logging
from nicegui import ui, run
import globals
import page_layout

logger = logging.getLogger("foundry_logger")


def _make_refresh(pb, rpm_labels, pwm_labels, watt_labels, shunt_labels, raw_labels):
    """Return an async refresh callback bound to a specific powerboard's UI labels."""
    async def refresh():
        try:
            r1, r2, r3 = pb.get_running_fan_pwm()
            rpm_labels[0].set_text(f'{pb.row1_rpm or 0:,}')
            rpm_labels[1].set_text(f'{pb.row2_rpm or 0:,}')
            rpm_labels[2].set_text(f'{pb.row3_rpm or 0:,}')
            pwm_labels[0].set_text(f'{r1}%')
            pwm_labels[1].set_text(f'{r2}%')
            pwm_labels[2].set_text(f'{r3}%')

            sec12 = pb.watt_sec_1_2 or 0
            sec34 = pb.watt_sec_3_4 or 0
            watt_labels[0].set_text(f'{sec12} W')
            watt_labels[1].set_text(f'{sec34} W')
            watt_labels[2].set_text(f'{sec12 + sec34} W')

            if pb._current_wattage and shunt_labels:
                for i, label in enumerate(shunt_labels):
                    label.set_text(f'{int(pb._current_wattage[i])} W')

            raw_labels['tach'].set_text(pb._raw_tach or '—')
            raw_labels['wattage'].set_text(pb._raw_wattage or '—')
            raw_labels['pwm'].set_text(pb._raw_pwm or '—')
            raw_labels['metadata'].set_text(pb._raw_metadata or '—')
            raw_labels['jumper'].set_text(pb._raw_jumper or '—')

        except Exception as e:
            logger.warning(f'Error refreshing powerboard {pb.location} display: {e}')
    return refresh


async def powerboardsPage():
    with page_layout.frame('Powerboards'):
        with ui.element('div').classes('p-6 w-full max-w-4xl'):

            if not globals.powerboardDict:
                with ui.card().classes('p-8 bg-[#1b1b1b]'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('power_off').classes('text-gray-500 text-3xl')
                        ui.label('No powerboards detected.').classes('text-gray-400 text-lg')
                return

            # ── Debug toggle ──────────────────────────────────────────────
            debug_toggle = ui.switch('Debug').classes('mb-4 text-gray-400')

            for pb_id, pb in sorted(globals.powerboardDict.items()):
                with ui.card().classes('w-full mb-6 bg-[#1b1b1b] p-0 overflow-hidden'):

                    # ── Header ────────────────────────────────────────────────
                    with ui.row().classes('w-full items-center justify-between px-5 py-3 bg-[#242424]'):
                        with ui.row().classes('items-center gap-3'):
                            ui.icon('memory').classes('text-[#ffdd00]')
                            ui.label(f'Powerboard {pb.location}').classes('text-lg font-bold')
                        connected = pb.is_connected
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('circle').classes(
                                f'text-xs {"text-green-400" if connected else "text-red-400"}'
                            )
                            ui.label('Connected' if connected else 'Disconnected').classes(
                                f'text-sm {"text-green-400" if connected else "text-red-400"}'
                            )

                    # ── Board identity ────────────────────────────────────────
                    with ui.row().classes('px-5 py-3 gap-8 flex-wrap'):
                        for label_text, value in [
                            ('Hardware Rev', pb.hardware_revision),
                            ('Firmware Ver', pb.firmware_version),
                            ('Serial Port',  pb._serial_instance.port),
                        ]:
                            with ui.column().classes('gap-0'):
                                ui.label(label_text).classes('text-xs text-gray-400 uppercase tracking-wide')
                                ui.label(value).classes('font-mono text-sm')

                    ui.separator()

                    # ── Fan headers ───────────────────────────────────────────
                    with ui.element('div').classes('px-5 pt-3 pb-1'):
                        ui.label('Fan Headers').classes(
                            'text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2'
                        )
                    with ui.grid(columns=4).classes('px-5 pb-4 gap-x-4 gap-y-1 w-full'):
                        # Column headers
                        ui.label('')
                        for row_name in ['Row 1', 'Row 2', 'Row 3']:
                            ui.label(row_name).classes('text-center text-sm font-semibold text-[#ffdd00]')

                        # RPM row
                        ui.label('RPM').classes('text-gray-400 text-sm self-center')
                        rpm_labels = [
                            ui.label(f'{pb.row1_rpm or 0:,}').classes('text-center font-mono'),
                            ui.label(f'{pb.row2_rpm or 0:,}').classes('text-center font-mono'),
                            ui.label(f'{pb.row3_rpm or 0:,}').classes('text-center font-mono'),
                        ]

                        # PWM row
                        ui.label('PWM').classes('text-gray-400 text-sm self-center')
                        r1, r2, r3 = pb.get_running_fan_pwm()
                        pwm_labels = [
                            ui.label(f'{r1}%').classes('text-center font-mono'),
                            ui.label(f'{r2}%').classes('text-center font-mono'),
                            ui.label(f'{r3}%').classes('text-center font-mono'),
                        ]

                        # Saved PWM row
                        ui.label('Saved PWM').classes('text-gray-400 text-sm self-center')
                        s1, s2, s3 = pb.get_saved_fan_pwm()
                        for val in [s1, s2, s3]:
                            ui.label(f'{val}%').classes('text-center font-mono text-gray-500')

                    ui.separator()

                    # ── Power ─────────────────────────────────────────────────
                    with ui.element('div').classes('px-5 pt-3 pb-1'):
                        ui.label('Power').classes(
                            'text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2'
                        )
                    sec12 = pb.watt_sec_1_2 or 0
                    sec34 = pb.watt_sec_3_4 or 0
                    with ui.row().classes('px-5 pb-3 gap-8 flex-wrap items-end'):
                        for label_text, initial, accent in [
                            ('Section 1–2', f'{sec12} W',          False),
                            ('Section 3–4', f'{sec34} W',          False),
                            ('Total',       f'{sec12 + sec34} W',  True),
                        ]:
                            with ui.column().classes('gap-0'):
                                ui.label(label_text).classes('text-xs text-gray-400 uppercase tracking-wide')
                                lbl = ui.label(initial).classes(
                                    f'font-mono text-lg {"text-[#ffdd00]" if accent else ""}'
                                )
                                if label_text == 'Section 1–2':
                                    watt_12_label = lbl
                                elif label_text == 'Section 3–4':
                                    watt_34_label = lbl
                                else:
                                    watt_total_label = lbl

                    # Per-shunt breakdown
                    shunt_labels = []
                    if pb._current_wattage:
                        with ui.element('div').classes('px-5 pb-4'):
                            ui.label('Shunt breakdown').classes('text-xs text-gray-500 mb-1')
                            with ui.row().classes('gap-6'):
                                for i, w in enumerate(pb._current_wattage):
                                    with ui.column().classes('gap-0'):
                                        ui.label(f'Shunt {i + 1}').classes('text-xs text-gray-500')
                                        shunt_labels.append(
                                            ui.label(f'{int(w)} W').classes('font-mono text-sm text-gray-300')
                                        )

                    ui.separator()

                    # ── Control mode (jumper) ─────────────────────────────────
                    try:
                        jumper = await run.io_bound(pb.get_jumper_state)
                        mode = 'Motherboard' if jumper == 1 else 'Powerboard'
                        mode_color = 'text-yellow-400' if jumper == 1 else 'text-green-400'
                    except Exception:
                        mode = 'Unknown'
                        mode_color = 'text-gray-500'

                    with ui.row().classes('px-5 py-3 items-center gap-2'):
                        ui.icon('tune').classes('text-gray-400 text-sm')
                        ui.label('Control Mode:').classes('text-sm text-gray-400')
                        ui.label(mode).classes(f'font-mono text-sm {mode_color}')

                    # ── Debug panel ───────────────────────────────────────────
                    with ui.element('div').classes('px-5 pb-4') as debug_panel:
                        ui.separator().classes('mb-3')
                        ui.label('Raw Serial').classes(
                            'text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2'
                        )
                        raw_labels = {}
                        for key, attr, label_text in [
                            ('tach',     '_raw_tach',      'Tach (T:)'),
                            ('wattage',  '_raw_wattage',   'Wattage (W:)'),
                            ('pwm',      '_raw_pwm',       'PWM (P:)'),
                            ('metadata', '_raw_metadata',  'Metadata (V:)'),
                            ('jumper',   '_raw_jumper',    'Jumper (J:)'),
                        ]:
                            with ui.row().classes('gap-3 items-baseline'):
                                ui.label(label_text).classes('text-xs text-gray-500 w-28')
                                raw_labels[key] = ui.label(
                                    getattr(pb, attr) or '—'
                                ).classes('font-mono text-xs text-gray-300')
                    debug_panel.bind_visibility_from(debug_toggle, 'value')

                    # ── Live refresh timer ────────────────────────────────────
                    ui.timer(3.0, _make_refresh(
                        pb,
                        rpm_labels,
                        pwm_labels,
                        [watt_12_label, watt_34_label, watt_total_label],
                        shunt_labels,
                        raw_labels,
                    ))
