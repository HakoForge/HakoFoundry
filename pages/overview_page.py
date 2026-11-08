from typing import Optional
from nicegui import app, ui
from authentication import require_auth
from foundry_state import Backplane, Drive
import xxhash
import page_layout
import globals

class DriveButton(ui.button):
    """Custom button class used to select and display drives.

    When a drive button is clicked, it gives the option to assign a drive if there is none.
    Once assigned, clicking on the drive will display basic data on the right drawer.
    Clicking VIEW ALL will pop up a window with raw smartctl data values if there are any.
    Each button is a child of a card element that represents a backplane.
    """

    def __init__(self, card, button_index, drive_hash) -> None:
        super().__init__()
        self.classes('drive-button')
        self.selected = False
        self.card = card
        self.button_index = button_index

        with self:
            with ui.row().classes('items-center gap-2 w-full overflow-hidden') as self.row_element:
                if drive_hash is None or drive_hash not in globals.drivesList:
                    self.assigned_drive = None
                    self.temp_label = ui.label().classes('flex-shrink-0 text-xs')
                    self.temp_label.set_visibility(False)
                    self.model_label_text = "----Empty----"
                    self.sn_label_text = ""
                    self.text_color = "gray"
                else:
                    self.assigned_drive: Drive = globals.drivesList.get(drive_hash)
                    self.temp_label = ui.label().bind_text_from(self.assigned_drive, 'temp', lambda temp: globals.format_temperature(temp)).classes('flex-shrink-0')
                    self.model_label_text = self.assigned_drive.model
                    self.sn_label_text = self.assigned_drive.serial_num
                    self.text_color = "white"

        # This will be set by the parent function
        self.on_click_handler = None

    def assign_drive(self, selection):
        """Assign a drive to this button from selection string."""
        sn = selection.split()[-1][1:-1]
        drive_hash = xxhash.xxh3_64(sn).intdigest()
        self.assigned_drive = globals.drivesList[drive_hash]

        if globals.layoutState.show_model:
            self.model_label.style('color: white')
            self.model_label.set_text(self.assigned_drive.model)
        else:
            self.model_label.set_visibility(False)
        if globals.layoutState.show_sn:
            self.sn_label.set_visibility(True)
            self.sn_label.style('color: white')
            self.sn_label.set_text(self.assigned_drive.serial_num)
        self.temp_label.set_visibility(True)
        self.temp_label.style('color: white')
        self.temp_label.bind_text_from(self.assigned_drive, 'temp', lambda temp: globals.format_temperature(temp))
        if hasattr(self, 'remove_btn'):
            self.remove_btn.set_visibility(True)
        if hasattr(self, 'remove_menu_item'):
            self.remove_menu_item.set_visibility(True)

    async def clear_drive(self):
        """Remove the assigned drive from this button."""
        globals.layoutState.remove_drive(self.card, self.assigned_drive.hash)
        self.assigned_drive = None
        self.model_label.style('color: gray').set_text('----Empty----')
        self.model_label.set_visibility(True)
        self.temp_label.set_visibility(False)
        self.sn_label.set_visibility(False)
        if hasattr(self, 'remove_btn'):
            self.remove_btn.set_visibility(False)
        if hasattr(self, 'remove_menu_item'):
            self.remove_menu_item.set_visibility(False)
        if self.on_click_handler:
            await self.on_click_handler(self)


class HDDButton(DriveButton):
    """Button styled for HDD drives."""

    def __init__(self, card, button_index, drive_hash) -> None:
        super().__init__(card, button_index, drive_hash)
        self.props('flat color="white" size="11px"').classes(
            'w-full my-0.5 border-solid border-2 truncate'
        ).style('height: 23%;')
        with self.row_element:
            with ui.column().classes('gap-0 overflow-hidden flex-1 min-w-0').style('display: inline-block;'):
                self.model_label = ui.label(self.model_label_text).style(f'color: {self.text_color}').classes(
                    'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
                ).style('display: block;')
                self.sn_label = ui.label(self.sn_label_text).style(f'color: {self.text_color}').classes(
                    'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
                ).style('display: block;')

                if not globals.layoutState.show_model and self.assigned_drive is not None:
                    self.model_label.set_visibility(False)
                if not globals.layoutState.show_sn or self.assigned_drive is None:
                    self.sn_label.set_visibility(False)

class SmlSSDButton(DriveButton):
    """Button styled for small SSD drives."""

    def __init__(self, card, button_index, drive_hash) -> None:
        super().__init__(card, button_index, drive_hash)
        self.props('flat color="white" align="left" size="11px"').classes(
            'w-2/3 my-0.5 px-2 p-1 border-solid border-2 truncate'
        ).style('height: 17%;')

        with self.row_element:
            self.model_label = ui.label(self.model_label_text).style(f'color: {self.text_color}').classes(
                'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
            ).style('display: block;')
            self.sn_label = ui.label(self.sn_label_text).style(f'color: {self.text_color}').classes(
                'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
            ).style('display: block; direction: rtl;')

            if not globals.layoutState.show_model and self.assigned_drive is not None:
                self.model_label.set_visibility(False)
            if not globals.layoutState.show_sn or self.assigned_drive is None:
                self.sn_label.set_visibility(False)

class StdSSDButton(DriveButton):
    """Button styled for standard SSD drives."""

    def __init__(self, card, button_index, drive_hash) -> None:
        super().__init__(card, button_index, drive_hash)
        self.props('flat color="white" size="11px"').classes(
            'w-full my-0.5 p-0.5 px-1 border-solid border-2 truncate'
        ).style('height: 14.9%;')

        with self.row_element:
            self.model_label = ui.label(self.model_label_text).style(f'color: {self.text_color}').classes(
                'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
            ).style('display: block;')
            self.sn_label = ui.label(self.sn_label_text).style(f'color: {self.text_color}').classes(
                'overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0'
            ).style('display: block; direction: rtl;')

            if not globals.layoutState.show_model and self.assigned_drive is not None:
                self.model_label.set_visibility(False)
            if not globals.layoutState.show_sn or self.assigned_drive is None:
                self.sn_label.set_visibility(False)

class FansRowButton(ui.button):
    """Button for fan row controls."""

    def __init__(self) -> None:
        super().__init__()
        self.selected = False

        with self.classes('h-1/3 w-full border-solid border-2 flex-1 content-center justify-center items-center w-full').props('flat color="white"'):
            ui.icon('mode_fan').classes('material-symbols-outlined')

class FanRowButtons(ui.element):

    def __init__(self, callback, grid_position: str):
        super().__init__()
        self.row_Of_Buttons = []
        # Use explicit grid positioning
        with self.classes('w-full').style(f'grid-area: {grid_position};'):
            with ui.element('div').classes('h-full flex flex-col p-3 mx-3 bg-neutral-900'):
                b1 = FansRowButton().classes('mb-3')
                b1.on_click(lambda b=b1: callback(b))
                b2 = FansRowButton().classes('mb-3')
                b2.on_click(lambda b=b2: callback(b))
                b3 = FansRowButton()
                b3.on_click(lambda b=b3: callback(b))

                self.row_Of_Buttons.extend([b1, b2, b3])

class RPMCard(ui.element):
    """Displays RPM for the fan wall assigned to this grid position."""

    def __init__(self, index, grid_position: str) -> None:
        super().__init__('div')
        wall_id = index + 1  # position 0 → wall 1, 1 → wall 2, 2 → wall 3
        rpm_attrs = ['row1_rpm', 'row2_rpm', 'row3_rpm']

        with self.classes('px-1 p-1 flex content-center justify-center items-center w-full border-solid border-white rounded-md border-2 bg-neutral-900').style(f'grid-area: {grid_position};'):
            wall = globals.fan_control_service.fan_walls.get(wall_id) if globals.fan_control_service else None
            pb = next((p for p in globals.powerboardDict.values() if p.location == wall.powerboard_id), None) if wall and wall.powerboard_id is not None else None
            if pb and wall.header_index is not None:
                attr = rpm_attrs[wall.header_index]
                self.RPMLabel = ui.label().bind_text_from(pb, attr, lambda rpm: f'{rpm} RPM')
            else:
                ui.label('N/A').classes('text-gray-500 italic')


class WattageCard(ui.element):
    """Displays wattage for the fan wall assigned to this grid position."""

    # Physical wattage section per header index (board-level, not remappable per header)
    _WATT_ATTRS = ['watt_sec_1_2', 'watt_sec_3_4', 'watt_sec_1_2', 'watt_sec_3_4']

    def __init__(self, index, grid_position: str) -> None:
        super().__init__('div')
        wall_id = index + 1  # position 0 → wall 1, 1 → wall 2, 2 → wall 3, 3 → wall 4

        with self.classes('px-1 p-1 flex content-center justify-center items-center w-full border-solid border-white rounded-md border-2 bg-neutral-900').style(f'grid-area: {grid_position};'):
            wall = globals.fan_control_service.fan_walls.get(wall_id) if globals.fan_control_service else None

            # Priority 1: explicit wattage source configured independently of fan control
            if wall and wall.watt_powerboard_id is not None and wall.watt_attr is not None:
                pb = next((p for p in globals.powerboardDict.values() if p.location == wall.watt_powerboard_id), None)
                watt_attr = wall.watt_attr
            else:
                # Priority 2: derive from fan wall assignment
                pb = next((p for p in globals.powerboardDict.values() if p.location == wall.powerboard_id), None) if wall and wall.powerboard_id is not None else None
                # Priority 3: fall back to pb1
                if pb is None:
                    pb = next((p for p in globals.powerboardDict.values() if p.location == 1), None)
                watt_attr = self._WATT_ATTRS[min(wall.header_index, len(self._WATT_ATTRS) - 1)] if wall and wall.header_index is not None else self._WATT_ATTRS[min(index, len(self._WATT_ATTRS) - 1)]

            if pb:
                row_label = wall.watt_label if (wall and wall.watt_label) else f'Row {index + 1}'
                self.watt_label = ui.label().bind_text_from(
                    pb, watt_attr, lambda w, lbl=row_label: f'{lbl}: {w} watts'
                )
            else:
                self.watt_label = ui.label('N/A').classes('text-gray-500 italic')


class AuxFanCard(ui.element):
    """Compact status card for auxiliary/unassigned fan walls shown in the bottom bar."""

    _RPM_ATTRS = ['row1_rpm', 'row2_rpm', 'row3_rpm']

    def __init__(self, wall_id: int) -> None:
        super().__init__('div')
        self.selected = False
        wall = globals.fan_control_service.fan_walls.get(wall_id) if globals.fan_control_service else None
        pb = next(
            (p for p in globals.powerboardDict.values() if p.location == wall.powerboard_id),
            None
        ) if wall and wall.powerboard_id is not None else None

        with self.classes(
            'flex flex-col items-center justify-center px-3 py-2 border-solid border-white '
            'rounded-md border-2 bg-neutral-900 cursor-pointer min-w-[110px] gap-0.5'
        ):
            if wall:
                ui.label(wall.name).classes('text-xs font-medium text-center leading-tight')
                if pb and wall.header_index is not None:
                    attr = self._RPM_ATTRS[wall.header_index]
                    ui.label().bind_text_from(pb, attr, lambda rpm: f'{rpm} RPM').classes('text-xs text-gray-400')
                else:
                    ui.label('Unassigned').classes('text-xs text-gray-500 italic')
                ui.label().bind_text_from(wall, 'current_speed', lambda s: f'{int(s)}%').classes('text-sm font-bold')
            else:
                ui.label(f'Zone {wall_id}').classes('text-xs text-gray-500 italic')


class StdPlaceHolderCard(ui.element):
    """Standard size card representing standard backplanes."""

    def __init__(self, index, backplane: Backplane, grid_position: str) -> None:
        super().__init__('div')
        self.index = index
        self.buttons = []
        self.tabsRight = True

        if globals.layoutState.get_product() == "Hako-Core":
            if (index % 3 == 1): # 2nd column
                self.tabsRight = False
        elif globals.layoutState.get_product() == "Hako-Core DAS":
            if (index % 4 in {1, 3}): # 2nd and 4th columns
                self.tabsRight = False
        elif globals.layoutState.get_product() == "Hako-Core Mini":
            if (index % 2 == 1): # 2nd column
                self.tabsRight = False
        # HF-L1 has only one column — tabsRight stays True

        with self.classes('p-0 flex').style(f'aspect-ratio: 1/1; width: 100%; height: 100%; grid-area: {grid_position};'):
            # Will be populated by parent function
            pass


class SmlPlaceHolderCard(ui.element):
    """Small size card representing small backplanes."""

    def __init__(self, index, backplane, grid_position: str) -> None:
        super().__init__('div')
        self.index = index
        self.buttons = []
        self.tabsRight = True

        if globals.layoutState.get_product() == "Hako-Core":
            if (index % 3 == 1): # 2nd column
                self.tabsRight = False
        elif globals.layoutState.get_product() == "Hako-Core DAS":
            if (index % 4 in {1, 3}): # 2nd and 4th columns
                self.tabsRight = False
        elif globals.layoutState.get_product() == "Hako-Core Mini":
            if (index % 2 == 1): # 2nd column
                self.tabsRight = False
        # HF-L1 has only one column — tabsRight stays True

        with self.classes('p-0 flex h-full').style(f'aspect-ratio: 100/87; width: 100%; max-height: 100%; grid-area: {grid_position};'):
            # Will be populated by parent function
            pass


class FadingDropdown(ui.element):
    """
    A fully integrated, chainable FadingDropdown component.
    This version correctly uses inheritance and manual component construction,
    and adds the correct `fading-dropdown` class so it can be counter-rotated
    in flipped backplane layouts.
    """

    def __init__(self,
                 text: str, *,
                 container_classes: str = 'w-full flex justify-center items-center rounded-xl border border-neutral-600',
                 button_color: Optional[str] = 'primary',
                 icon: Optional[str] = None,
                 ) -> None:
        """
        :param text: The text to be displayed on the button.
        :param container_classes: Tailwind classes for the surrounding container div.
        :param button_color: The color of the button.
        :param icon: The name of an icon to be displayed on the button.
        """
        super().__init__('div')

        # Important: add fading-dropdown here in the SAME call
        self.classes(container_classes + ' fading-dropdown').style('width: 87%;')

        self.is_visible = False
        self.hide_timer: Optional[ui.timer] = None

        with self:
            self.button = (
                ui.button(text, color=button_color, icon=icon)
                .props('outline color="white"')
                .classes('fading-dropdown-btn opacity-0 transition-opacity duration-300')
                .style('visibility: hidden;')
            )

            with self.button:
                self.menu = ui.menu().props('fit')

        # Hover events
        self.on('mouseover', self._handle_show)
        self.on('mouseleave', self._handle_hide)
        self.menu.on('mouseover', self._handle_show)
        self.menu.on('mouseleave', self._handle_hide)

    def _update_visibility_classes(self) -> None:
        """Update the button's opacity classes based on current state."""
        if self.is_visible:
            self.button.classes(add='opacity-100', remove='opacity-0')
            self.button.style('visibility: visible;')
        else:
            self.button.classes(add='opacity-0', remove='opacity-100')
            self.button.style('visibility: hidden;')

    def _handle_show(self) -> None:
        """Show the button and cancel any pending hide timer."""
        if self.hide_timer:
            self.hide_timer.cancel()
        self.is_visible = True
        self._update_visibility_classes()

    def _handle_hide(self) -> None:
        """Start a timer to hide the button after a short delay."""
        if self.hide_timer:
            self.hide_timer.cancel()
        self.hide_timer = ui.timer(0.1, self._set_hidden, once=True)

    def _set_hidden(self) -> None:
        """Hide the button and reset the timer."""
        self.is_visible = False
        self.hide_timer = None
        self._update_visibility_classes()

class ChassisLayoutManager:
    """Manages chassis layout configurations and grid positioning."""

    def __init__(self):
        self.layouts = {
            "Hako-Core": {
                "normal": {
                    "grid_template_areas": """
                        "rpm1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt3 watt3 watt3 watt3 watt3 watt3 watt3 rpm3"
                        "fan1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp3 bp3 bp3 bp3 bp3 bp3 bp3 fan3"
                        "fan1 bp4 bp4 bp4 bp4 bp4 bp4 bp4 fan2 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp6 bp6 bp6 bp6 bp6 bp6 bp6 fan3"
                        "fan1 bp7 bp7 bp7 bp7 bp7 bp7 bp7 fan2 bp8 bp8 bp8 bp8 bp8 bp8 bp8 bp9 bp9 bp9 bp9 bp9 bp9 bp9 fan3"
                        "fan1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml3 sml3 sml3 sml3 sml3 sml3 sml3 fan3"
                    """,
                    "fan_positions": ["fan1", "fan2", "fan3"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6", "bp7", "bp8", "bp9"],
                    "small_positions": ["sml1", "sml2", "sml3"],
                    "rpm_positions": ["rpm1", "rpm2", "rpm3"],
                    "watt_positions": ["watt1", "watt2", "watt3"]
                },
                "inverted": {
                    "grid_template_areas": """
                        "rpm3 watt3 watt3 watt3 watt3 watt3 watt3 watt3 watt2 watt2 watt2 watt2 watt2 watt2 watt2 rpm2 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm1"
                        "fan3 sml3 sml3 sml3 sml3 sml3 sml3 sml3 sml2 sml2 sml2 sml2 sml2 sml2 sml2 fan2 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan1"
                        "fan3 bp9 bp9 bp9 bp9 bp9 bp9 bp9 bp8 bp8 bp8 bp8 bp8 bp8 bp8 fan2 bp7 bp7 bp7 bp7 bp7 bp7 bp7 fan1"
                        "fan3 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp5 bp5 bp5 bp5 bp5 bp5 bp5 fan2 bp4 bp4 bp4 bp4 bp4 bp4 bp4 fan1"
                        "fan3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp2 bp2 bp2 bp2 bp2 bp2 bp2 fan2 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan1"
                    """,
                    "fan_positions": ["fan1", "fan2", "fan3"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6", "bp7", "bp8", "bp9"],
                    "small_positions": ["sml1", "sml2", "sml3"],
                    "rpm_positions": ["rpm1", "rpm2", "rpm3"],
                    "watt_positions": ["watt1", "watt2", "watt3"]
                }
            },
            "Hako-Core DAS": {
                # 3 fan walls, 4 drive columns (cols 1+3 share orientation, cols 2+4 share orientation)
                # Grid uses 23 columns: fan(1) + col(5) + fan(1) + col(5) + col(5) + fan(1) + col(5)
                "normal": {
                    "grid_template_areas": """
                        "rpm1 watt1 watt1 watt1 watt1 watt1 rpm2 watt2 watt2 watt2 watt2 watt2 watt3 watt3 watt3 watt3 watt3 rpm3 watt4 watt4 watt4 watt4 watt4"
                        "fan1 bp1 bp1 bp1 bp1 bp1 fan2 bp2 bp2 bp2 bp2 bp2 bp3 bp3 bp3 bp3 bp3 fan3 bp4 bp4 bp4 bp4 bp4"
                        "fan1 bp5 bp5 bp5 bp5 bp5 fan2 bp6 bp6 bp6 bp6 bp6 bp7 bp7 bp7 bp7 bp7 fan3 bp8 bp8 bp8 bp8 bp8"
                        "fan1 bp9 bp9 bp9 bp9 bp9 fan2 bp10 bp10 bp10 bp10 bp10 bp11 bp11 bp11 bp11 bp11 fan3 bp12 bp12 bp12 bp12 bp12"
                        "fan1 sml1 sml1 sml1 sml1 sml1 fan2 sml2 sml2 sml2 sml2 sml2 sml3 sml3 sml3 sml3 sml3 fan3 sml4 sml4 sml4 sml4 sml4"
                    """,
                    "fan_positions": ["fan1", "fan2", "fan3"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6", "bp7", "bp8", "bp9", "bp10", "bp11", "bp12"],
                    "small_positions": ["sml1", "sml2", "sml3", "sml4"],
                    "rpm_positions": ["rpm1", "rpm2", "rpm3"],
                    "watt_positions": ["watt1", "watt2", "watt3", "watt4"]
                },
                "inverted": {
                    "grid_template_areas": """
                        "watt4 watt4 watt4 watt4 watt4 rpm3 watt3 watt3 watt3 watt3 watt3 watt2 watt2 watt2 watt2 watt2 rpm2 watt1 watt1 watt1 watt1 watt1 rpm1"
                        "sml4 sml4 sml4 sml4 sml4 fan3 sml3 sml3 sml3 sml3 sml3 sml2 sml2 sml2 sml2 sml2 fan2 sml1 sml1 sml1 sml1 sml1 fan1"
                        "bp12 bp12 bp12 bp12 bp12 fan3 bp11 bp11 bp11 bp11 bp11 bp10 bp10 bp10 bp10 bp10 fan2 bp9 bp9 bp9 bp9 bp9 fan1"
                        "bp8 bp8 bp8 bp8 bp8 fan3 bp7 bp7 bp7 bp7 bp7 bp6 bp6 bp6 bp6 bp6 fan2 bp5 bp5 bp5 bp5 bp5 fan1"
                        "bp4 bp4 bp4 bp4 bp4 fan3 bp3 bp3 bp3 bp3 bp3 bp2 bp2 bp2 bp2 bp2 fan2 bp1 bp1 bp1 bp1 bp1 fan1"
                    """,
                    "fan_positions": ["fan1", "fan2", "fan3"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6", "bp7", "bp8", "bp9", "bp10", "bp11", "bp12"],
                    "small_positions": ["sml1", "sml2", "sml3", "sml4"],
                    "rpm_positions": ["rpm1", "rpm2", "rpm3"],
                    "watt_positions": ["watt1", "watt2", "watt3", "watt4"]
                }
            },
            "Hako-Core Mini": {
                "normal": {
                    "grid_template_areas": """
                        "rpm1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2"
                        "fan1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2"
                        "fan1 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 fan2 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4"
                        "fan1 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 fan2 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6"
                        "fan1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2"
                    """,
                    "fan_positions": ["fan1", "fan2"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6"],
                    "small_positions": ["sml1", "sml2"],
                    "rpm_positions": ["rpm1", "rpm2"],
                    "watt_positions": ["watt1", "watt2"]
                },
                "inverted": {
                    "grid_template_areas": """
                        "watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 watt2 rpm2 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm1"
                        "sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 sml2 fan2 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan1"
                        "bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 bp6 fan2 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 bp5 fan1"
                        "bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 bp4 fan2 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 fan1"
                        "bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 fan2 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan1"
                    """,
                    "fan_positions": ["fan1", "fan2"],
                    "backplane_positions": ["bp1", "bp2", "bp3", "bp4", "bp5", "bp6"],
                    "small_positions": ["sml1", "sml2"],
                    "rpm_positions": ["rpm1", "rpm2"],
                    "watt_positions": ["watt1", "watt2"]
                }
            },
            "HF-L1": {
                "normal": {
                    "grid_template_areas": """
                        "rpm1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm2"
                        "fan1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan2"
                        "fan1 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 fan2"
                        "fan1 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 fan2"
                        "fan1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan2"
                    """,
                    "fan_positions": ["fan1", "fan2"],
                    "backplane_positions": ["bp1", "bp2", "bp3"],
                    "small_positions": ["sml1"],
                    "rpm_positions": ["rpm1", "rpm2"],
                    "watt_positions": ["watt1"]
                },
                "inverted": {
                    "grid_template_areas": """
                        "rpm2 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 watt1 rpm1"
                        "fan2 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 sml1 fan1"
                        "fan2 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 bp3 fan1"
                        "fan2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 bp2 fan1"
                        "fan2 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 bp1 fan1"
                    """,
                    "fan_positions": ["fan1", "fan2"],
                    "backplane_positions": ["bp1", "bp2", "bp3"],
                    "small_positions": ["sml1"],
                    "rpm_positions": ["rpm1", "rpm2"],
                    "watt_positions": ["watt1"]
                }
            }
        }

    def get_layout_config(self, chassis_type: str, orientation: str = "normal"):
        """Get layout configuration for chassis type and orientation."""
        return self.layouts.get(chassis_type, {}).get(orientation, {})

    def get_grid_template_areas(self, chassis_type: str, orientation: str = "normal"):
        """Get CSS grid-template-areas string for the layout."""
        config = self.get_layout_config(chassis_type, orientation)
        return config.get("grid_template_areas", "")

class SystemOverview:
    """Main class to handle the system overview page functionality."""

    def __init__(self):
        """Initialize the SystemOverview with all necessary state variables."""
        # Global state variables
        self.fan_buttons_list = []
        self.wattage_card_list = []
        self.slider_list = [None] * 6
        self.last_button = None
        self.right_drawer = None
        self.fan_change_dialog = None
        self.layout_manager = ChassisLayoutManager()

        # Use the global fan control service instance
        self.fan_control_service = globals.fan_control_service

        # Ensure the fan control service is initialized
        if self.fan_control_service is None:
            print("Warning: Fan control service not initialized, initializing now...")
            globals.initFanControlService()
            self.fan_control_service = globals.fan_control_service

    def should_flip_backplane(self, i: int) -> bool:
        if not globals.layoutState.chassis_is_inverted():
            return False
        chassis = globals.layoutState.get_product()
        if chassis == "Hako-Core":
            return i in {0, 1, 3, 4, 6, 7, 9, 10}
        if chassis == "Hako-Core DAS":
            return i in {0, 1, 2, 4, 5, 6, 8, 9, 10}
        if chassis == "Hako-Core Mini":
            return i in {0, 1, 2, 3, 4, 5, 6, 7}
        if chassis == "HF-L1":
            return i in {0, 1, 2, 3}
        return False

    def should_rotate_backplane(self, index: int) -> bool:
        if not globals.layoutState.chassis_is_inverted():
            return False
        chassis = globals.layoutState.get_product()
        if chassis == "Hako-Core":
            return index in {0,1, 3,4, 6,7, 9,10}
        if chassis == "Hako-Core DAS":
            return index in {0,1,2, 4,5,6, 8,9,10}
        if chassis == "Hako-Core Mini":
            return index in {0,1, 2,3, 4,5}
        if chassis == "HF-L1":
            return index in {0, 1, 2, 3}
        return False

    def set_slider_value_without_callback(self, slider_index: int, value: float):
        """Set slider value without triggering the change callback."""
        if slider_index < len(self.slider_list) and self.slider_list[slider_index]:
            self.fan_control_service.set_slider_value_without_callback(self.slider_list[slider_index], value)

    def display_full_drive_attributes(self, drive):
        """Display full drive attributes in a dialog."""
        with ui.dialog() as attribute_window, ui.card().props('w-full'):
            ui.table(rows=drive.get_attribute_list(), column_defaults={'align': 'left'}).style('width: 50dvh')
        attribute_window.open()

    def toggle_drive_buttons(self, button):
        """Toggle selection state of drive buttons."""
        if button.selected:  # If selected, deselect and change to white
            button.classes('border-white', remove='border-[#ffdd00]')
            button.selected = False
        else:  # If deselected, select and change to yellow
            button.classes('border-[#ffdd00]', remove='border-white')
            button.selected = True

    async def toggle_fan_buttons(self):
        """Toggle selection state of fan buttons."""
        for button in self.fan_buttons_list:
            if button.selected:  # If selected, deselect and change to white
                button.classes('border-white', remove='border-[#ffdd00]')
                button.selected = False
                self.last_button = button
            else:  # If deselected, select and change to yellow
                button.classes('border-[#ffdd00]', remove='border-white')
                button.selected = True

    async def request_update_fan_speed(self):
        """Request fan speed update with semaphore protection."""
        await self.fan_control_service.request_update_fan_speed(self.slider_list)

    async def request_update_auxiliary_fan_speed(self):
        """Request auxiliary fan speed update with UI queue protection for second powerboard."""
        await self.fan_control_service.request_update_auxiliary_fan_speed(self.slider_list)

    async def set_fan_speed(self):
        """Set and save fan speed for both powerboards."""
        await self.fan_control_service.set_fan_speed(self.slider_list)

    async def dialog_handler_discard(self):
        """Handle discarding fan speed changes for both powerboards."""
        await self.fan_control_service.dialog_handler_discard(
            self.slider_list,
            self.request_update_fan_speed,
            self.request_update_auxiliary_fan_speed
        )

    async def select_fans(self, button):
        """Handle fan selection and drawer display."""
        if self.last_button is None:  # Initial click
            await self.toggle_fan_buttons()
            self.right_drawer.show()
            self.last_button = button
        elif self.last_button in self.fan_buttons_list:  # Fan button clicked
            await self.toggle_fan_buttons()
            self.right_drawer.hide()
            self.last_button = None
            return
        else:  # Last button was a drive
            self.toggle_drive_buttons(self.last_button)
            await self.toggle_fan_buttons()
            self.last_button = button

        self.setup_fan_drawer()

    def display_profile_sensors(self, profile_name: str, container_classes: str = ''):
        """Display temperature sensors and their current values for a given profile."""
        if not globals.fan_profile_service:
            return

        profile = globals.fan_profile_service.get_profile_by_name(profile_name)
        if not profile:
            return

        # Get all curves from the profile
        curves = profile.get_all_curves()
        sensors_displayed = set()  # Track which sensors we've already displayed

        # Helper function to format sensor display names
        def format_sensor_display_name(sensor_name):
            """Format sensor name for display (remove 'Drives.' prefix for cleaner display)."""
            if sensor_name.startswith('Drives.'):
                return sensor_name[7:]  # Remove "Drives." prefix for display
            return sensor_name

        for curve_id, curve in curves.items():
            if curve.sensor and curve.sensor not in sensors_displayed:
                sensors_displayed.add(curve.sensor)

                # Display sensor info with dynamically updating temperature
                with ui.element('div').classes(container_classes):
                    with ui.row().classes('w-full items-center justify-between text-sm text-gray-300'):
                        ui.label(f"{format_sensor_display_name(curve.sensor)}").classes('flex-grow')

                        # Create a label that updates dynamically
                        temp_label = ui.label("N/A").classes('font-mono')

                        # Function to update temperature
                        def update_temp(sensor_name=curve.sensor, label=temp_label):
                            try:
                                current_temp = globals.fan_profile_service.get_sensor_temperature(sensor_name) if globals.fan_profile_service else None
                                temp_display = globals.format_temperature(current_temp) if current_temp is not None else "N/A"
                                label.set_text(temp_display)
                            except Exception:
                                label.set_text("Error")

                        # Initial update
                        update_temp()

                        # Set up timer to update every 3 seconds
                        ui.timer(3.0, update_temp)

        # If no sensors were displayed (all curves have no sensors assigned), show "No sensors" message
        if not sensors_displayed:
            with ui.element('div').classes(container_classes):
                with ui.row().classes('w-full items-center justify-between text-sm text-gray-300'):
                    ui.label("No sensors").classes('flex-grow italic')
                    ui.label("N/A").classes('font-mono italic')

    def setup_fan_drawer(self):
        """Set up the fan control drawer."""
        with self.right_drawer:
            self.right_drawer.clear()

            if not globals.powerboardDict:
                with ui.row().classes('w-full justify-center p-12'):
                    ui.label('No powerboards detected.').classes('text-gray-500 italic')
                return

            profile_options = self.fan_control_service.get_fan_profile_options()

            # wall_id → slider_list index
            wall_slider_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}
            first_visible = True

            for wall_id, slider_idx in wall_slider_map.items():
                wall = self.fan_control_service.fan_walls.get(wall_id)
                if not wall:
                    continue
                # Only show walls whose assigned powerboard is connected
                if wall.powerboard_id not in globals.powerboardDict:
                    continue

                if not first_visible:
                    ui.separator()
                first_visible = False

                # Header row: wall name + assignment subtitle + manual checkbox
                header_label = wall.name
                if wall.powerboard_id is not None and wall.header_index is not None:
                    assignment_sub = f'PB{wall.powerboard_id} · Row {wall.header_index + 1}'
                else:
                    assignment_sub = 'Unassigned'

                with ui.row().classes('w-full items-center justify-between px-5 mt-3'):
                    with ui.column().classes('gap-0'):
                        ui.label(header_label).classes('font-medium')
                        ui.label(assignment_sub).classes('text-xs text-gray-500')
                    manual_checkbox = ui.checkbox('Manual', value=wall.manual).classes('text-sm')

                with ui.element('div').classes('px-5 pt-3 w-full'):
                    self.slider_list[slider_idx] = ui.slider(min=20, max=100).props('label-always')
                    self.slider_list[slider_idx].bind_value(wall, 'current_speed')
                    self.slider_list[slider_idx].set_enabled(wall.manual)

                profile_container = ui.element('div').classes('px-5 pb-2 w-full')
                with profile_container:
                    profile_select = ui.select(
                        options=profile_options,
                        label='Fan Profile',
                        value=wall.assigned_profile if wall.assigned_profile in profile_options else (profile_options[0] if profile_options else None)
                    ).classes('w-full')
                    profile_select.set_enabled(not wall.manual)

                if wall.manual:
                    profile_container.set_visibility(False)

                if wall.assigned_profile and wall.assigned_profile != 'None' and not wall.manual:
                    self.display_profile_sensors(wall.assigned_profile, 'px-5 pb-2 w-full')

                def make_toggle(wid, sidx, psel, pcont):
                    def toggle(e):
                        self.slider_list[sidx].set_enabled(e.value)
                        psel.set_enabled(not e.value)
                        pcont.set_visibility(not e.value)
                        self.fan_control_service.set_manual_mode(wid, e.value)
                        if not e.value and profile_options:
                            psel.set_value(profile_options[0])
                            self.fan_control_service.assign_profile_to_wall(wid, profile_options[0])
                        self.setup_fan_drawer()
                    return toggle

                def make_profile_select(wid, mcb):
                    def on_select(e):
                        if not mcb.value:
                            self.fan_control_service.assign_profile_to_wall(wid, e.value)
                            self.setup_fan_drawer()
                    return on_select

                manual_checkbox.on_value_change(make_toggle(wall_id, slider_idx, profile_select, profile_container))
                profile_select.on_value_change(make_profile_select(wall_id, manual_checkbox))

                ui.separator()

    def display_drive_attributes(self, button: DriveButton):
        """Display drive attributes in the right drawer."""
        self.right_drawer.clear()

        with self.right_drawer:
            columns = [
                {'name': 'attribute', 'label': 'Attribute', 'field': 'attribute', 'required': True, 'align': 'left'},
                {'name': 'value', 'label': 'Value', 'field': 'value', 'required': True, 'align': 'right'},
            ]

            d = button.assigned_drive
            rows = [
                {'attribute': 'Model', 'value': d.model},
                {'attribute': 'SN', 'value': d.serial_num},
                {'attribute': 'Firmware', 'value': d.firmware_ver},
                {'attribute': 'Capacity', 'value': d.capacity},
                {'attribute': 'Rotation Speed', 'value': d.rotate_rate},
                {'attribute': 'Power On Time', 'value': d.on_time},
                {'attribute': 'Start Stop Count', 'value': d.power_cycle},
                {'attribute': 'Temp', 'value': globals.format_temperature(d.temp)}
            ]

            with ui.item().props('clickable v-ripple').classes('w-full bg-[#ffdd00]').on(
                'mouseenter', lambda: edit_icon.set_visibility(True)
            ).on('mouseleave', lambda: edit_icon.set_visibility(False)):
                with ui.item_section():
                    ui.item_label(d.model).style('color: black')
                with ui.item_section().props('avatar'):
                    edit_icon = ui.icon('edit').props('color=black').classes('material-symbols-outlined')
                    edit_icon.set_visibility(False)
                with ui.menu().props('fit'):
                    ui.menu_item('Remove drive', lambda: button.clear_drive())

            ui.table(columns=columns, rows=rows, row_key='attribute').classes('w-full')
            with ui.element('dive').classes('w-full px-4'):
                ui.button(
                    "Show All",
                    icon='open_in_new',
                    on_click=lambda: self.display_full_drive_attributes(d)
                ).classes('w-full border-solid border-2 border-[#ffdd00]').props('flat color="white"')

    async def select_drive(self, button: DriveButton):
        """Handle drive selection and drawer display."""
        if self.last_button is None:  # Initial click
            self.toggle_drive_buttons(button)
            self.right_drawer.show()
            self.last_button = button
        elif self.last_button in self.fan_buttons_list:  # Last click was fans
            await self.toggle_fan_buttons()
            self.toggle_drive_buttons(button)
            self.last_button = button
        elif self.last_button == button:  # Same button clicked, deselect
            self.toggle_drive_buttons(button)
            self.right_drawer.hide()
            self.last_button = None
        else:  # General switching selection
            self.toggle_drive_buttons(self.last_button)
            self.toggle_drive_buttons(button)
            self.last_button = button

        if button.assigned_drive is None:  # No drive assigned, display options
            self.setup_drive_assignment_drawer(button)
        else:
            self.display_drive_attributes(button)

    def setup_drive_assignment_drawer(self, button: DriveButton):
        """Set up the drive assignment drawer."""
        with self.right_drawer:
            self.right_drawer.clear()

            with ui.item().classes('w-full bg-[#ffdd00]'):
                with ui.item_section():
                    ui.item_label("Assign Drive").style('color: black')
            with ui.element('div').classes('p-4 w-full'):
                ui.select(
                    label="Select or search drive",
                    options=[
                        globals.drivesList[k].model + ' (' + globals.drivesList[k].serial_num + ')'
                        for k in globals.drivesList
                    ],
                    with_input=True,
                    on_change=lambda e: (
                        button.assign_drive(e.value),
                        globals.layoutState.insert_drive(button.card, e.value, button.button_index),
                        self.display_drive_attributes(button)
                    )
                ).classes('w-full')

    def _attach_drive_button_controls(self, button):
        """Add hover remove button and right-click context menu to a drive button."""
        button.classes(add='relative')
        has_drive = button.assigned_drive is not None
        with button:
            button.remove_btn = ui.button(
                icon='close',
                on_click=lambda b=button: b.clear_drive()
            ).props('flat round dense size=xs color=grey-5').classes(
                'absolute top-0 right-0 z-10 opacity-30 hover:opacity-100'
            ).tooltip('Remove drive')
            button.remove_btn.set_visibility(has_drive)

            with ui.context_menu():
                button.remove_menu_item = ui.menu_item(
                    'Remove Disk',
                    on_click=lambda b=button: b.clear_drive()
                )
                button.remove_menu_item.set_visibility(has_drive)

    def setup_backplane_buttons(self, card, backplane: Backplane, index):
        """Set up buttons for different backplane types (flip parent container in inverted mode)."""
        card.clear()
        cage = ""  # "" (default) or "-rotated"
        backplane_type = backplane.product if backplane else None
        if card.tabsRight is False:  # 2nd row: use rotated cage orientation
            cage = "-rotated"

        # --- determine if this backplane should be rotated in inverted orientation ---
        def should_flip(i: int) -> bool:
            if not globals.layoutState.chassis_is_inverted():
                return False
            chassis = globals.layoutState.get_product()
            if chassis == "Hako-Core":
                return i in {0, 1, 2, 3, 4, 6, 7, 9, 10}
            if chassis == "Hako-Core DAS":
                return i in {0, 1, 2, 4, 5, 6, 8, 9, 10}
            if chassis == "Hako-Core Mini":
                return i in {0, 1, 2, 3, 4, 5}
            return False

        need_flip = globals.layoutState.chassis_is_inverted()

        # order for SML2+2 - check if we need reversed order for inverted mode
        def should_reverse_sml_order():
            """Check if SML2+2 backplane should have reversed button order (SSDs first)."""
            if not globals.layoutState.chassis_is_inverted():
                return False
            if backplane_type != "SML2+2":
                return False
            # If backplane is NOT getting visually flipped but we're in inverted mode,
            # reverse the button order so SSDs end up on top
            return not need_flip

        if should_reverse_sml_order():
            sml_button_order = [SmlSSDButton, SmlSSDButton, HDDButton, HDDButton]
        else:
            sml_button_order = [HDDButton, HDDButton, SmlSSDButton, SmlSSDButton]

        backplane_configs = {
            "STD4HDD": {"buttons": 4, "button_class": HDDButton, "layout": "single_column"},
            "STD12SSD": {"buttons": 12, "button_class": StdSSDButton, "layout": "two_column"},
            "SML2+2": {"buttons": 4, "button_class": sml_button_order, "layout": "mixed"},
        }

        if not backplane_type or backplane_type not in backplane_configs:
            return

        config = backplane_configs[backplane_type]

        # --- apply flip on the parent card element ---
        card.classes(add="bp-rotatable relative" + (" flip-180" if need_flip else ""))

        with card:
            if config["layout"] == "single_column":
                with ui.element('div').classes(
                    f"f-shape{cage} h-full flex items-center justify-center p-1"
                ):
                    with ui.element('col').classes('col h-full'):
                        for i in range(config["buttons"]):
                            button = config["button_class"](card, i, backplane.drives_hashes[i])
                            button.classes('drive-button')
                            button.on_click_handler = self.select_drive
                            button.on('click', lambda b=button: self.select_drive(b))
                            self._attach_drive_button_controls(button)
                            card.buttons.append(button.classes('truncate'))
                    ui.element('div').classes(f'extension-patch patch-top-arm-bottom{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-top{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-bottom{cage}')

            elif config["layout"] == "two_column":
                with ui.element('div').classes(
                    f"f-shape{cage} grid grid-cols-2 gap-1 flex items-center justify-center h-full p-1"
                ):
                    with ui.element('col1').classes('col-span-1 h-full'):
                        for i in range(6):
                            button = config["button_class"](card, i, backplane.drives_hashes[i])
                            button.classes('drive-button')
                            button.on_click_handler = self.select_drive
                            button.on('click', lambda b=button: self.select_drive(b))
                            self._attach_drive_button_controls(button)
                            card.buttons.append(button.classes('truncate'))
                    with ui.element('col2').classes('col-span-1 h-full'):
                        for i in range(6, 12):
                            button = config["button_class"](card, i, backplane.drives_hashes[i])
                            button.classes('drive-button')
                            button.on_click_handler = self.select_drive
                            button.on('click', lambda b=button: self.select_drive(b))
                            self._attach_drive_button_controls(button)
                            card.buttons.append(button.classes('truncate'))
                    ui.element('div').classes(f'extension-patch patch-top-arm-bottom{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-top{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-bottom{cage}')

            elif config["layout"] == "mixed":
                with ui.element('div').classes(
                    f"f-shape{cage} h-full flex items-center justify-center p-1"
                ):
                    with ui.element('col').classes('col h-full flex justify-center'):
                        for i in range(4):
                            cls = config["button_class"][i]
                            button = cls(card, i, backplane.drives_hashes[i])
                            button.classes('drive-button')
                            button.on_click_handler = self.select_drive
                            button.on('click', lambda b=button: self.select_drive(b))
                            if cls == SmlSSDButton:
                                button.props('no-wrap')
                            else:
                                button.style('height: 28%;')
                            self._attach_drive_button_controls(button)
                            card.buttons.append(button)
                    ui.element('div').classes(f'extension-patch patch-top-arm-bottom{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-top{cage}')
                    ui.element('div').classes(f'extension-patch patch-mid-arm-bottom{cage}')

            with ui.dialog().props('persistent') as confirm_dialog, ui.card().classes('p-6'):
                ui.label('Change Backplane?').classes('text-xl font-bold mb-4')
                ui.label('This will remove the backplane and all its drive assignments.').classes('text-sm text-gray-400 mb-4')
                with ui.row().classes('w-full justify-center gap-4'):
                    ui.button('Yes, Remove', on_click=lambda c=card: (
                        globals.layoutState.remove_backplane(c),
                        self.add_backplane_button(c, c.__class__),
                        confirm_dialog.close()
                    )).classes('border-solid border-2 border-red-500 text-red-500 px-6 py-2').props('flat')
                    ui.button('Cancel', on_click=confirm_dialog.close).classes('border-solid border-2 border-[#ffdd00] text-white px-6 py-2').props('flat')

            ui.button(
                icon='swap_horiz',
                on_click=confirm_dialog.open
            ).props('flat round dense size=xs color=grey-5').classes(
                'absolute top-0 right-0 z-10 opacity-30 hover:opacity-100'
            ).tooltip('Change backplane type')

            with ui.context_menu():
                ui.menu_item(
                    'Remove Backplane',
                    on_click=confirm_dialog.open
                )

    def add_backplane_button(self, card, card_class):
        card.clear()
        element_justified = ""
        if card.tabsRight is False:
            element_justified = " justify-content:end;"

        # DO NOT remove bp-rotatable/flip-180; just leave classes as-is
        for button in card.buttons:
            if button.selected:
                self.last_button = None
                self.right_drawer.hide()
        card.buttons.clear()

        with card.style(f'{element_justified}'):
            with FadingDropdown('Add Backplane', icon='add').menu:
                if card_class == StdPlaceHolderCard:
                    ui.menu_item(
                        '4 HDD/U.2 Backplane',
                        on_click=lambda: self.setup_backplane_buttons(
                            card, globals.layoutState.insert_backplane(card, "STD4HDD"), card.index
                        )
                    )
                    ui.menu_item(
                        '12 SSD Backplane',
                        on_click=lambda: self.setup_backplane_buttons(
                            card, globals.layoutState.insert_backplane(card, "STD12SSD"), card.index
                        )
                    )
                else:
                    ui.menu_item(
                        '2+2 Backplane',
                        on_click=lambda: self.setup_backplane_buttons(
                            card, globals.layoutState.insert_backplane(card, "SML2+2"), card.index
                        )
                    )

    def create_chassis_layout(self, card: ui.element, chassis_type: str):
        if globals.layoutState.get_product() is None:
            globals.layoutState.set_product(chassis_type)

        card.clear()
        self.fan_buttons_list.clear()
        self.wattage_card_list.clear()

        is_inverted = globals.layoutState.chassis_is_inverted()
        orientation = "inverted" if is_inverted else "normal"
        layout_config = self.layout_manager.get_layout_config(chassis_type, orientation)
        if not layout_config:
            print(f"No layout config found for {chassis_type} {orientation}")
            return

        grid_template_areas = self.layout_manager.get_grid_template_areas(chassis_type, orientation)

        with card:
            # Set width based on chassis type
            if chassis_type == "Hako-Core Mini":
                card_width = '50dvw'
            elif chassis_type == "HF-L1":
                card_width = '35dvw'
            elif chassis_type == "Hako-Core DAS":
                card_width = '90dvw'
            else:
                card_width = '70dvw'
            # Set grid template rows based on orientation (all chassis share the same 5-row structure)
            grid_rows = '4% 21% 25% 25% 25%' if is_inverted else '4% 25% 25% 25% 21%'
            with ui.element('div').classes('gap-0').style(
                f'height: 98.9dvh; width: {card_width}; min-width: 800px; min-height: 800px; '
                f'display: grid; grid-template-areas: {grid_template_areas}; '
                f'grid-template-rows: {grid_rows}; '
                f'grid-template-columns: repeat({"23" if chassis_type == "Hako-Core DAS" else "24"}, 1fr);'
            ):

                def wall_assigned(wall_id: int) -> bool:
                    if not globals.fan_control_service:
                        return False
                    wall = globals.fan_control_service.fan_walls.get(wall_id)
                    return wall is not None and wall.powerboard_id is not None

                # Wattage always renders — physical powerboard sections are always present
                for i, position in enumerate(layout_config["watt_positions"]):
                    self.wattage_card_list.append(WattageCard(i, position))
                # RPM and fan buttons only render if the wall has a powerboard assigned
                for i, position in enumerate(layout_config["rpm_positions"]):
                    if wall_assigned(i + 1):
                        RPMCard(i, position)
                for i, position in enumerate(layout_config["fan_positions"]):
                    if wall_assigned(i + 1):
                        fan_row = FanRowButtons(self.select_fans, position)
                        self.fan_buttons_list.extend(fan_row.row_Of_Buttons)

                # Backplanes
                if not globals.layoutState.is_empty():
                    backplane_list = globals.layoutState.get_backplanes()

                    # STD cards
                    for i, position in enumerate(layout_config["backplane_positions"]):
                        bp = backplane_list[i] if i < len(backplane_list) else None
                        card_widget = StdPlaceHolderCard(i, bp, position)
                        # flip the parent card NOW, even if empty
                        card_widget.classes(add="bp-rotatable" + (" flip-180" if globals.layoutState.chassis_is_inverted() else ""))
                        if bp:
                            self.setup_backplane_buttons(card_widget, bp, i)
                        else:
                            self.add_backplane_button(card_widget, StdPlaceHolderCard)

                    # SML cards
                    start_idx = len(layout_config["backplane_positions"])
                    for i, position in enumerate(layout_config["small_positions"]):
                        bp_index = start_idx + i
                        bp = backplane_list[bp_index] if bp_index < len(backplane_list) else None
                        card_widget = SmlPlaceHolderCard(bp_index, bp, position)
                        card_widget.classes(add="bp-rotatable" + (" flip-180" if globals.layoutState.chassis_is_inverted() else ""))
                        if bp:
                            self.setup_backplane_buttons(card_widget, bp, bp_index)
                        else:
                            self.add_backplane_button(card_widget, SmlPlaceHolderCard)
                else:
                    # Empty STD cards
                    for i, position in enumerate(layout_config["backplane_positions"]):
                        card_widget = StdPlaceHolderCard(i, None, position)
                        card_widget.classes(add="bp-rotatable" + (" flip-180" if globals.layoutState.chassis_is_inverted() else ""))
                        self.add_backplane_button(card_widget, StdPlaceHolderCard)

                    # Empty SML cards
                    start_idx = len(layout_config["backplane_positions"])
                    for i, position in enumerate(layout_config["small_positions"]):
                        idx = start_idx + i
                        card_widget = SmlPlaceHolderCard(idx, None, position)
                        card_widget.classes(add="bp-rotatable" + (" flip-180" if globals.layoutState.chassis_is_inverted() else ""))
                        self.add_backplane_button(card_widget, SmlPlaceHolderCard)

    def show_chassis_selection_dialog(self, main_content):
        """Show a dialog for chassis selection."""
        with ui.dialog().props('persistent') as chassis_dialog, ui.card().classes('p-6'):
            ui.label('Select Chassis').classes('text-2xl font-bold mb-4')
            with ui.row().classes('w-full justify-center gap-4'):
                def select_hako_core():
                    chassis_dialog.close()
                    self.create_chassis_layout(main_content, "Hako-Core")

                def select_hako_core_das():
                    chassis_dialog.close()
                    self.create_chassis_layout(main_content, "Hako-Core DAS")

                def select_hako_core_mini():
                    chassis_dialog.close()
                    self.create_chassis_layout(main_content, "Hako-Core Mini")

                def select_hf_l1():
                    chassis_dialog.close()
                    self.create_chassis_layout(main_content, "HF-L1")

                ui.button(
                    'Hako-Core',
                    on_click=select_hako_core
                ).classes('border-solid border-2 border-[#ffdd00] px-8 py-4').props('flat color="white"')

                ui.button(
                    'Hako-Core DAS',
                    on_click=select_hako_core_das
                ).classes('border-solid border-2 border-[#ffdd00] px-8 py-4').props('flat color="white"')

                ui.button(
                    'Hako-Core Mini',
                    on_click=select_hako_core_mini
                ).classes('border-solid border-2 border-[#ffdd00] px-8 py-4').props('flat color="white"')

                ui.button(
                    'HF-L1',
                    on_click=select_hf_l1
                ).classes('border-solid border-2 border-[#ffdd00] px-8 py-4').props('flat color="white"')

        chassis_dialog.open()

    def create_ui(self):
        """Create the main UI."""
        with page_layout.frame('System Overview'):
            with ui.element('div').classes('flex w-full').style('justify-content: safe center;'):
                # Apply appropriate CSS class based on chassis orientation
                css_class = 'pseudo-extend-inverted' if globals.layoutState.chassis_is_inverted() else 'pseudo-extend'
                with ui.element('div').classes(css_class) as main_content:
                    current_chassis = globals.layoutState.get_product()

                    if current_chassis is None:
                        # Show chassis selection dialog
                        self.show_chassis_selection_dialog(main_content)
                    elif current_chassis in ["Hako-Core", "Hako-Core DAS", "Hako-Core Mini", "HF-L1"]:
                        self.create_chassis_layout(main_content, current_chassis)

            # Auxiliary / unassigned fan zones bar — below chassis grid, in normal page flow
            if globals.fan_control_service:
                aux_wall_ids = [
                    wid for wid in [4, 5, 6]
                    if wid in globals.fan_control_service.fan_walls
                    and globals.fan_control_service.fan_walls[wid].powerboard_id is not None
                ]
                if aux_wall_ids:
                    with ui.row().classes('gap-2 p-2 justify-center w-full'):
                        for wid in aux_wall_ids:
                            card = AuxFanCard(wid)
                            self.fan_buttons_list.append(card)
                            card.on('click', lambda c=card: self.select_fans(c))

            # Create drawers and dialogs
            self.right_drawer = ui.right_drawer(value=False, fixed=True).style().props(
                'bordered width="490"'
            ).classes('p-0')

            with ui.dialog() as self.fan_change_dialog, ui.card():
                ui.label('Apply changes?')
                ui.button(
                    'Apply',
                    on_click=lambda: self.fan_change_dialog.submit("Apply")
                ).on_click(self.set_fan_speed)
                ui.button(
                    'Discard',
                    on_click=lambda: self.fan_change_dialog.submit("Discard")
                ).on_click(self.dialog_handler_discard)

@require_auth
def overviewPage():
    """Page with helper-functions that interact with the powerboard and use smartctl to display GUI.

    The powerboard is an object that gets refreshed every 3 seconds for new values and the respective
    UI elements are updated. During the refresh, the powerboard is unable to take commands for 2 seconds.
    Fan control commands are queued if the powerboard is busy. A toast notification will popup indicating
    a fan command has been executed.

    The drive information is taken from smartctl commands so S.M.A.R.T. must be enabled on the drives to
    be shown.
    """
    # Set up static file serving for CSS
    app.add_static_files('/css', 'css')

    # Add CSS file references
    ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/f-shape.css">')
    ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/f-shape-rotated.css">')
    ui.add_head_html('<link rel="stylesheet" type="text/css" href="/css/pseudo-extend.css">')

    overview = SystemOverview()
    overview.create_ui()
