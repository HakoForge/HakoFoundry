import all_pages
import api  # noqa: F401 – registers API routes with the NiceGUI/FastAPI app
import authentication
import globals
import os
import hashlib
from nicegui import ui

@ui.page('/')
def index_page() -> None:
    if not authentication.user_manager.user_exists():
        ui.navigate.to('/register')
    elif authentication.validate_environment():
        authentication.create_login_page()
    else:
        ui.navigate.to('/overview')

@ui.page('/register')
def register_page() -> None:
    """Registration page for new users."""
    authentication.create_registration_page()

@ui.page('/login')
def login_page() -> None:
    """Login page for existing users."""
    authentication.create_login_page()

all_pages.create()

# Initializing things that only run once.
if __name__ == '__mp_main__':
    globals.initLayout()
    globals.initPowerboard()
    globals.initTempBackend()  # Initialize the global temperature sensor backend FIRST
    globals.initFanProfileBackend()   # Initialize the global fan control backend SECOND (depends on temp backend)
    globals.initFanControlService()  # Initialize the global fan control service THIRD (depends on powerboards and fan backend)
    if os.getenv('DEBUG') and os.getenv('DEBUG').upper() == 'TRUE':
        globals.initDrives(True)
    else:
        globals.initDrives()

    # No authentication provided, open site
    authentication.validate_environment()

ui.run(title="Hako Foundry", favicon="res/Hako_Logo.png", dark=True, storage_secret=hashlib.sha256(os.getenv('SECRET').encode()).hexdigest())