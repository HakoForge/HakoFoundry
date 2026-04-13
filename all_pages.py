from nicegui import ui
from pages.overview_page import overviewPage
from pages.settings_page import settingsPage
from pages.fan_curve_page import fanCurvePage
from pages.powerboards_page import powerboardsPage
"""Class for mapping all pages."""
def create() -> None:
    ui.page('/overview')(overviewPage)
    ui.page('/settings')(settingsPage)
    ui.page('/curves')(fanCurvePage)
    ui.page('/powerboards')(powerboardsPage)