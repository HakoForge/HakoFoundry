import all_pages
import api  # noqa: F401 – registers API routes with the NiceGUI/FastAPI app
import authentication
import globals
import os
import hashlib
import logging
import secrets
import prometheus_metrics
from nicegui import ui

STORAGE_SECRET_FILE = 'config/storage_secret'
logger = logging.getLogger('foundry_logger')


def _resolve_storage_secret() -> str:
    """Return the NiceGUI storage secret: sha256(SECRET), or a generated secret persisted in config/."""
    # Any set SECRET, including empty, is hashed so sessions stay valid; unset falls back to a persisted generated
    # secret (0600) because NiceGUI storage requires a secret and startup would otherwise crash.
    secret_env = os.getenv('SECRET')
    if secret_env is not None:
        return hashlib.sha256(secret_env.encode()).hexdigest()

    if os.path.exists(STORAGE_SECRET_FILE):
        with open(STORAGE_SECRET_FILE, 'r') as f:
            stored = f.read().strip()
        if stored:
            return hashlib.sha256(stored.encode()).hexdigest()

    token = secrets.token_hex(32)
    tmp_path = STORAGE_SECRET_FILE + '.tmp'
    try:
        os.makedirs(os.path.dirname(STORAGE_SECRET_FILE), exist_ok=True)
        # O_EXCL below fails on a temp file left by an interrupted start.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        # Created with 0600 so the secret is never readable by others, then renamed into place
        # so a crash mid-write cannot leave a truncated or empty secret file.
        fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(token)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, STORAGE_SECRET_FILE)
    except OSError as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError as cleanup_error:
            logger.error(f"Could not remove temporary storage secret {tmp_path}: {cleanup_error}")
        logger.error(f"SECRET is unset and {STORAGE_SECRET_FILE} could not be written ({e}); "
                     f"using a per-process secret, sessions will not survive a reload")
        return hashlib.sha256(secrets.token_hex(32).encode()).hexdigest()
    logger.warning(f"SECRET is unset; generated a persistent storage secret at {STORAGE_SECRET_FILE}")
    return hashlib.sha256(token.encode()).hexdigest()


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
prometheus_metrics.register_metrics_endpoint()

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

ui.run(title="Hako Foundry", favicon="res/Hako_Logo.png", dark=True, storage_secret=_resolve_storage_secret(), port=int(os.getenv('PORT', '8080')))
