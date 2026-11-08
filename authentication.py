import logging
import json
import os
import stat
import secrets
import time
from typing import Dict, Optional
from functools import wraps
from nicegui import ui, app
from passlib.context import CryptContext

# Configure logging
logger = logging.getLogger("foundry_logger")

# Some autofill tools set the login fields without an input event. When focus leaves a field, Quasar re-renders it
# from its (still empty) model and wipes the text; a capture-phase focusout runs before that re-render, so it
# re-dispatches an input event to push the browser's value into the model first.
_SYNC_AUTOFILLED_LOGIN_JS = """<script>
document.addEventListener('focusout', (e) => {
  const t = e.target;
  if (t instanceof HTMLInputElement && (t.name === 'username' || t.name === 'password') && t.value) {
    t.dispatchEvent(new Event('input', { bubbles: true }));
  }
}, true);
</script>"""

# Initialize password context for hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Config file for user storage
USER_CONFIG_FILE = "config/user_config.json"

# Enable open access mode (skip authentication)
OPEN_ACCESS_MODE = os.getenv('OPEN_ACCESS', 'false').lower() in ('true', '1', 'yes', 'on')

class UserManager:
    """Manages user authentication and storage."""
    
    def __init__(self):
        self.config_file = USER_CONFIG_FILE
        self._ensure_config_dir()
    
    def _ensure_config_dir(self):
        """Ensure config directory exists."""
        config_dir = os.path.dirname(self.config_file)
        if not os.path.exists(config_dir):
            os.makedirs(config_dir)
    
    def _load_user_config(self) -> Dict:
        """Load user configuration from file."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading user config: {e}")
            return {}
    
    def _save_user_config(self, config: Dict):
        """Save user configuration to file with restrictive permissions."""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)
            
            # Set restrictive permissions (owner read/write only - 600)
            # This prevents other users on the system from reading the hash file
            try:
                os.chmod(self.config_file, stat.S_IRUSR | stat.S_IWUSR)
                logger.debug(f"Set restrictive permissions (600) on {self.config_file}")
            except OSError as perm_error:
                logger.warning(f"Could not set file permissions: {perm_error}")
            
            logger.info("User configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving user config: {e}")
    
    def user_exists(self) -> bool:
        """Check if a user is already registered."""
        config = self._load_user_config()
        return 'username_hash' in config and 'password_hash' in config
    
    def register_user(self, username: str, password: str) -> bool:
        """Register a new user (only if no user exists)."""
        if self.user_exists():
            logger.warning("User already exists, cannot register new user")
            return False
        
        try:
            # Hash both username and password
            username_hash = pwd_context.hash(username)
            password_hash = pwd_context.hash(password)
            config = {
                'username_hash': username_hash,
                'password_hash': password_hash,
                'created_at': time.time()
            }
            self._save_user_config(config)
            
            # Store in NiceGUI storage as well (store original username for display)
            app.storage.general['user_registered'] = True
            app.storage.general['username'] = username
            
            logger.info(f"User '{username}' registered successfully")
            return True
        except Exception as e:
            logger.error(f"Error registering user: {e}")
            return False
    
    def verify_credentials(self, username: str, password: str) -> bool:
        """Verify username and password."""
        config = self._load_user_config()
        
        if not config or 'username_hash' not in config or 'password_hash' not in config:
            return False
        
        try:
            # Verify both username and password hashes
            username_valid = pwd_context.verify(username, config['username_hash'])
            password_valid = pwd_context.verify(password, config['password_hash'])
            
            return username_valid and password_valid
        except Exception as e:
            logger.error(f"Error verifying credentials: {e}")
            return False
    
    def get_username(self) -> Optional[str]:
        """Get the registered username from NiceGUI storage."""
        # Since username is now hashed in config, we get it from storage
        # or return a placeholder if not available
        try:
            return app.storage.general.get('username', 'User')
        except:
            return 'User'

# Global user manager instance
user_manager = UserManager()

valid_sessions: Dict[str, Dict] = {}  # session_token -> {username, created_at, last_seen}

def verify_credentials(username: str, password: str) -> bool:
    """Verify username and password using the user manager."""
    return user_manager.verify_credentials(username, password)

def get_session_token() -> str:
    """Get session token from browser storage (naturally isolated per browser)."""
    try:
        # app.storage.browser is automatically different for each browser instance
        session_token = app.storage.browser.get('session_token')
        if not session_token:
            # Create new session token for this browser
            session_token = secrets.token_urlsafe(32)
            app.storage.browser['session_token'] = session_token
            logger.info(f"Created new session token for browser: {session_token[:8]}...")
        return session_token
    except Exception as e:
        logger.error(f"Error accessing browser storage: {e}")
        return secrets.token_urlsafe(32)

def is_authenticated() -> bool:
    """Check if current browser session is authenticated."""
    # Check for open access mode first
    if OPEN_ACCESS_MODE:
        logger.debug("Open access mode enabled - skipping authentication")
        return True
    
    # If no user is registered, redirect to registration
    if not user_manager.user_exists():
        logger.debug("No user registered - requiring registration")
        return False
    
    try:
        # Get session token from THIS browser's storage
        session_token = get_session_token()
        
        # Check if this session token is valid in our global session store
        if session_token not in valid_sessions:
            logger.debug(f"Valid session not found: {session_token[:8]}...")
            return False
        
        # Update last seen time
        valid_sessions[session_token]['last_seen'] = time.time()
        
        username = valid_sessions[session_token]['username']
        logger.debug(f"Valid session for {username} in: {session_token[:8]}...")
        return True
        
    except Exception as e:
        logger.error(f"Error in authenticating: {e}")
        return False

def authenticate_session(username: str):
    """Mark current browser session as authenticated."""
    try:
        session_token = get_session_token()
        valid_sessions[session_token] = {
            'username': username,
            'created_at': time.time(),
            'last_seen': time.time()
        }
        try:
            # Browser storage can't be written once the page response has been sent;
            # the session above is what authentication relies on.
            app.storage.browser['username'] = username
        except Exception:
            pass
        
        logger.info(f"Authenticated session for {username}: {session_token[:8]}...")
        
    except Exception as e:
        logger.error(f"Error in authenticating session: {e}")

def logout_session():
    """Remove authentication for current browser session."""
    try:
        session_token = get_session_token()
        if session_token in valid_sessions:
            username = valid_sessions[session_token]['username']
            del valid_sessions[session_token]
            logger.info(f"Logged out {username}: {session_token[:8]}...")
        app.storage.browser.clear()
        
    except Exception as e:
        logger.error(f"Error logging out: {e}")

def get_current_user() -> str:
    """Get current authenticated username."""
    # Check for open access mode first
    if OPEN_ACCESS_MODE:
        return 'Guest (Open Access)'
    
    # If no user registered, return guest
    if not user_manager.user_exists():
        return 'Guest'
    
    try:
        username = app.storage.browser.get('username')
        if username:
            return username
        session_token = get_session_token()
        session_data = valid_sessions.get(session_token, {})
        return session_data.get('username', 'Unknown')
    except:
        return 'Unknown'

def cleanup_expired_sessions(max_age_hours: int = 24):
    """Clean up old sessions."""
    current_time = time.time()
    expired_sessions = []
    
    for token, data in valid_sessions.items():
        age_hours = (current_time - data['last_seen']) / 3600
        if age_hours > max_age_hours:
            expired_sessions.append(token)
    
    for token in expired_sessions:
        username = valid_sessions[token]['username']
        del valid_sessions[token]
        logger.info(f"Cleaned up expired session for {username}: {token[:8]}...")

def create_login_page():
    """Create login page UI."""

    async def handle_login():
        # Password managers fill the browser's fields directly, and the server-side
        # value can lag behind that, so read what is in the fields right now.
        username, password = username_input.value or '', password_input.value or ''
        try:
            vals = await ui.run_javascript(
                f'const q = id => {{ const r = document.getElementById("c" + id); const i = r && (r.tagName === "INPUT" ? r : r.querySelector("input")); return i ? i.value : ""; }};'
                f'return [q({username_input.id}), q({password_input.id})];', timeout=3.0)
            if isinstance(vals, list) and len(vals) == 2:
                username, password = vals[0] or username, vals[1] or password
        except Exception as e:
            logger.debug(f"Login: could not read field values from the browser ({e})")
        username = username.strip()
        if not password:
            # Nothing entered yet: move to the password field instead of trying an empty login
            password_input.run_method('focus')
            return
        
        logger.info(f"Login attempt: {username}")
        
        if not user_manager.user_exists():
            error_label.text = 'No user registered. Please register first.'
            error_label.visible = True
            return
        
        if verify_credentials(username, password):
            logger.info(f"Credentials valid for {username}")
            authenticate_session(username)
            
            # Redirect to main page
            ui.timer(0.1, lambda: ui.navigate.to('/overview'), once=True)
        else:
            logger.warning(f"Invalid credentials for {username}")
            error_label.text = 'Invalid credentials'
            error_label.visible = True
            password_input.value = ''
    
    def handle_register_redirect():
        """Redirect to registration page."""
        ui.navigate.to('/register')
    
    with ui.column().classes('absolute-center'):
        with ui.card().classes('p-8').style('width: 30vw'):
            with ui.row().classes('w-full'):
                with ui.link(target='http://hakoforge.com', new_tab=True).classes('w-full'):
                    ui.image('res/Foundry_Logo.png')
                
                # Show different content based on whether user exists
                if not user_manager.user_exists():
                    ui.label('No user registered').classes('text-center text-lg mb-4')
                    ui.button('Register New User', on_click=handle_register_redirect).classes('w-full mb-4 border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                    
                    # Show open access option if enabled
                    if OPEN_ACCESS_MODE:
                        ui.button('Skip Registration (Open Access)', 
                                 on_click=lambda: ui.navigate.to('/overview')).classes('w-full mb-4 border-solid border-2 border-gray-400').props('flat color="gray"')
                    
                    # Add social media links and return early
                    ui.space().classes('h-6')
                    with ui.row().classes('justify-center w-full'):
                        with ui.link(target='https://discord.com/invite/3kjpbmckgm'):
                            ui.image('res/Discord_Icon.png').style('width: 50px')
                        with ui.link(target='https://www.reddit.com/r/hakoforge/'):
                            ui.image('res/Reddit_Icon.png').style('width: 50px')
                        with ui.link(target='https://www.youtube.com/@HakoForge'):
                            ui.image('res/YouTube_Icon.png').style('width: 50px')
                        with ui.link(target='https://www.instagram.com/hakoforge'):
                            ui.image('res/Instagram_Icon.png').style('width: 50px')
                        with ui.link(target='https://twitter.com/HakoForge'):
                            ui.image('res/X_Icon.png').style('width: 50px')
                        with ui.link(target='https://www.tiktok.com/@hakoforge'):
                            ui.image('res/TikTok_Icon.png').style('width: 50px')
                        with ui.link(target='https://github.com/hakoforge'):
                            ui.image('res/Git_Icon.png').style('width: 50px')
                    return
                
                # Normal login form for existing user
                username_input = ui.input('Username').classes('w-full mb-4').props('autocomplete=username name=username autofocus')
                password_input = ui.input('Password', password=True).classes('w-full mb-4').props('autocomplete=current-password name=password')
                ui.add_body_html(_SYNC_AUTOFILLED_LOGIN_JS)
                
                error_label = ui.label('').classes('text-red-500 text-sm mb-4')
                error_label.visible = False
                
                ui.button('Login', on_click=handle_login).classes('w-full border-solid border-2 border-[#ffdd00]').props('flat color="white"')
                password_input.on('keydown.enter', handle_login)
                username_input.on('keydown.enter', lambda: password_input.run_method('focus'))
                ui.space().classes('h-6')
            with ui.row().classes('justify-center w-full'):
                with ui.link(target='https://discord.com/invite/3kjpbmckgm'):
                    ui.image('res/Discord_Icon.png').style('width: 50px')
                with ui.link(target='https://www.reddit.com/r/hakoforge/'):
                    ui.image('res/Reddit_Icon.png').style('width: 50px')
                with ui.link(target='https://www.youtube.com/@HakoForge'):
                    ui.image('res/YouTube_Icon.png').style('width: 50px')
                with ui.link(target='https://www.instagram.com/hakoforge'):
                    ui.image('res/Instagram_Icon.png').style('width: 50px')
                with ui.link(target='https://twitter.com/HakoForge'):
                    ui.image('res/X_Icon.png').style('width: 50px')
                with ui.link(target='https://www.tiktok.com/@hakoforge'):
                    ui.image('res/TikTok_Icon.png').style('width: 50px')
                with ui.link(target='https://github.com/hakoforge'):
                    ui.image('res/Git_Icon.png').style('width: 50px')

def create_registration_page():
    """Create registration page UI for new user."""
    
    def handle_registration():
        username = username_input.value.strip()
        password = password_input.value
        confirm_password = confirm_password_input.value
        
        # Validation
        if not username:
            error_label.text = 'Username is required'
            error_label.visible = True
            return
        
        if len(username) < 3:
            error_label.text = 'Username must be at least 3 characters'
            error_label.visible = True
            return
        
        if not password:
            error_label.text = 'Password is required'
            error_label.visible = True
            return
        
        if len(password) < 6:
            error_label.text = 'Password must be at least 6 characters'
            error_label.visible = True
            return
        
        if password != confirm_password:
            error_label.text = 'Passwords do not match'
            error_label.visible = True
            return
        
        # Check if user already exists
        if user_manager.user_exists():
            error_label.text = 'User already registered'
            error_label.visible = True
            return
        
        # Register user
        if user_manager.register_user(username, password):
            success_label.text = 'Registration successful! Redirecting to login...'
            success_label.visible = True
            error_label.visible = False
            
            # Redirect to login after 2 seconds
            ui.timer(2.0, lambda: ui.navigate.to('/login'), once=True)
        else:
            error_label.text = 'Registration failed. Please try again.'
            error_label.visible = True
    
    def handle_back_to_login():
        """Go back to login if user exists."""
        ui.navigate.to('/login')
    
    with ui.column().classes('absolute-center'):
        with ui.card().classes('p-8').style('width: 30vw'):
            with ui.row().classes('w-full'):
                with ui.link(target='http://hakoforge.com', new_tab=True).classes('w-full'):
                    ui.image('res/Foundry_Logo.png')
                
                ui.label('Register New User').classes('text-center text-xl mb-4')
                
                # Show open access option if enabled
                if OPEN_ACCESS_MODE:
                    ui.button('Skip Registration (Open Access)', 
                             on_click=lambda: ui.navigate.to('/overview')).classes('w-full border-solid border-2 border-gray-400 mb-4').props('flat color="gray"')
                
                username_input = ui.input('Username').classes('w-full mb-4')
                password_input = ui.input('Password', password=True).classes('w-full mb-4')
                confirm_password_input = ui.input('Confirm Password', password=True).classes('w-full mb-4')
                
                error_label = ui.label('').classes('text-red-500 text-sm mb-4')
                error_label.visible = False
                
                success_label = ui.label('').classes('text-green-500 text-sm mb-4')
                success_label.visible = False
                
                ui.button('Register', on_click=handle_registration).classes('w-full border-solid border-2 border-[#ffdd00] mb-2').props('flat color="white"')
                
                # Show back to login button if user already exists
                if user_manager.user_exists():
                    ui.button('Back to Login', on_click=handle_back_to_login).classes('w-full border-solid border-2 border-gray-400').props('flat color="gray"')
                
                confirm_password_input.on('keydown.enter', handle_registration)
                ui.space().classes('h-6')
            
            with ui.row().classes('justify-center w-full'):
                with ui.link(target='https://discord.com/invite/3kjpbmckgm'):
                    ui.image('res/Discord_Icon.png').style('width: 50px')
                with ui.link(target='https://www.reddit.com/r/hakoforge/'):
                    ui.image('res/Reddit_Icon.png').style('width: 50px')
                with ui.link(target='https://www.youtube.com/@HakoForge'):
                    ui.image('res/YouTube_Icon.png').style('width: 50px')
                with ui.link(target='https://www.instagram.com/hakoforge'):
                    ui.image('res/Instagram_Icon.png').style('width: 50px')
                with ui.link(target='https://twitter.com/HakoForge'):
                    ui.image('res/X_Icon.png').style('width: 50px')
                with ui.link(target='https://www.tiktok.com/@hakoforge'):
                    ui.image('res/TikTok_Icon.png').style('width: 50px')
                with ui.link(target='https://github.com/hakoforge'):
                    ui.image('res/Git_Icon.png').style('width: 50px')

def require_auth(page_func):
    """Decorator to require authentication for a page."""
    @wraps(page_func)
    def wrapper(*args, **kwargs):
        logger.debug(f"Checking auth for {page_func.__name__}")
        
        # Check for open access mode first
        if OPEN_ACCESS_MODE:
            logger.debug("Open access mode enabled - bypassing authentication")
            return page_func(*args, **kwargs)
        
        # If no user is registered, redirect to registration
        if not user_manager.user_exists():
            logger.debug("No user registered, redirecting to registration")
            ui.navigate.to('/register')
            return
        
        # If user exists but not authenticated, show login
        if not is_authenticated():
            logger.debug("Not authenticated, showing login")
            create_login_page()
            return
            
        logger.debug(f"Authenticated, showing {page_func.__name__}")
        return page_func(*args, **kwargs)
    return wrapper
    
# Periodic cleanup of old sessions
ui.timer(3600, lambda: cleanup_expired_sessions(24), active=True)  # Clean up every hour

# Startup validation
def validate_environment() -> bool:
    """Validate environment configuration on startup."""
    if OPEN_ACCESS_MODE:
        logger.warning("OPEN ACCESS MODE: Authentication disabled!")
        logger.warning("The site is accessible without login.")
        return False  # Return False to skip login page
    elif not user_manager.user_exists():
        logger.warning("REGISTRATION REQUIRED: No user registered!")
        logger.warning("The site will require registration before access.")
        return False  # Return False to indicate no user registered
    else:
        username = user_manager.get_username()
        logger.info(f"AUTHENTICATION ENABLED: User '{username}' registered")
        logger.info("   Site is protected and requires login.")
        return True  # Return True to indicate user is registered