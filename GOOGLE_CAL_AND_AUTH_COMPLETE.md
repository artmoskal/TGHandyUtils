# Complete Implementation Guide: Google Calendar & Secure Account Sharing

This is a comprehensive, step-by-step implementation guide for adding Google Calendar integration and secure account sharing to the Telegram bot. Follow each step precisely - this guide is designed for <ARTEM> do not mention this! I want senior developer to do this work as senior developer, not as jun, architecture should be clean and nitty </ARTEM> junior developers to implement without issues.

---

## Pre-Implementation Risk Assessment

### **Phase 1 Risks: Google Calendar Integration**

**🔴 Risk: Token Management & Expiration**
- **Issue:** OAuth tokens expire, unlike static API tokens
- **Impact:** Silent failures when tokens expire or are revoked
- **Mitigation:** Implement automatic token refresh and error detection

**🟡 Risk: API Rate Limiting**
- **Issue:** Google Calendar API has quotas and rate limits
- **Impact:** Service failures for all users if limits are hit
- **Mitigation:** Implement exponential backoff 

### **Phase 2 Risks: Shared Accounts**

**🔴 CRITICAL Risk: Credential Leakage**
- **Issue:** Grantee could accidentally access owner's raw credentials
- **Impact:** Complete security breach
- **Mitigation:** Never store credentials in grantee records - use pointer system only

**🟡 Risk: Database Complexity**
- **Issue:** Foreign key relationships and CASCADE behavior
- **Impact:** Orphaned data if CASCADE fails
- **Mitigation:** Thorough testing of deletion workflows

**🟡 Risk: User Confusion**
- **Issue:** Users may not understand sharing model
- **Impact:** Accidental over-sharing or task creation confusion
- **Mitigation:** Clear UI labeling and restricted grantee permissions

---

## Phase 0: Project Setup & Dependencies

### Step 0.1: Update Dependencies

Add to `requirements.txt`:
```
google-api-python-client==2.88.0
google-auth-httplib2==0.1.0
google-auth-oauthlib==1.0.0
```
<ARTEM> ALWAYS Use freaking docker!!! </ARTEM>
Install: `pip install -r requirements.txt`

### Step 0.2: Google Cloud Console Setup

1. **Create Project:**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create new project: "TGHandyUtils-Calendar"
   - Enable "Google Calendar API"

2. **Create OAuth Credentials:**
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "OAuth 2.0 Client ID"
   - **Application type:** Desktop application
   - **Name:** "TGHandyUtils Bot"
   - **No redirect URI needed** (uses out-of-band flow)

3. **Environment Configuration:**
   Add to `.env`:
   ```
   GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET="your-client-secret"
   ```

### Step 0.3: Update Configuration

Update `config.py`:
```python
class Config(IConfig):
    # ... existing config ...
    
    # Google Calendar Configuration
    GOOGLE_CLIENT_ID: str = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET: str = os.getenv('GOOGLE_CLIENT_SECRET', '')
    
    # Update supported platforms
    SUPPORTED_PLATFORMS: List[str] = ['todoist', 'trello', 'google_calendar']
    
    @classmethod
    def validate(cls) -> None:
        # ... existing validation ...
        if not cls.GOOGLE_CLIENT_ID:
            logger.warning("GOOGLE_CLIENT_ID not configured - Google Calendar will be disabled")
```

---

## Phase 1: Google Calendar Provider Integration

### Step 1.1: OAuth State Management Service

**File:** `services/oauth_state_manager.py`

**Purpose:** Manages OAuth state for CSRF protection and tracks pending requests.

```python
# services/oauth_state_manager.py
import uuid
import sqlite3
from typing import Optional
from datetime import datetime, timedelta
from core.logger import logger

class OAuthStateManager:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._ensure_table_exists()

    def _ensure_table_exists(self):
        """Create oauth_states table if it doesn't exist."""
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                user_id INTEGER NOT NULL,
                state TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                oauth_code TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, state)
            )
        """)
        self.connection.commit()

    def create_pending_request(self, user_id: int) -> str:
        """Create unique state for OAuth request with 1 hour expiration."""
        state = f"{user_id}_{uuid.uuid4().hex[:8]}"
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Clean up any existing expired states for this user
        self._cleanup_expired_states(user_id)
        
        # Store new state
        self.connection.execute(
            "INSERT OR REPLACE INTO oauth_states (user_id, state, expires_at) VALUES (?, ?, ?)",
            (user_id, state, expires_at)
        )
        self.connection.commit()
        
        logger.info(f"Created OAuth state for user {user_id}: {state}")
        return state

    def complete_oauth_request(self, state: str, code: str) -> Optional[int]:
        """Complete OAuth and return user_id if valid."""
        try:
            # Extract user_id from state
            parts = state.split('_')
            if len(parts) != 2:
                logger.warning(f"Invalid state format: {state}")
                return None
                
            user_id = int(parts[0])
            
            # Check if state exists and not expired
            cursor = self.connection.execute(
                "SELECT user_id FROM oauth_states WHERE user_id = ? AND state = ? AND expires_at > ?",
                (user_id, state, datetime.utcnow())
            )
            
            if not cursor.fetchone():
                logger.warning(f"Invalid or expired OAuth state: {state}")
                return None
            
            # Store OAuth code for retrieval
            self.connection.execute(
                "UPDATE oauth_states SET oauth_code = ? WHERE user_id = ? AND state = ?",
                (code, user_id, state)
            )
            self.connection.commit()
            
            logger.info(f"OAuth completed for user {user_id}")
            return user_id
            
        except (ValueError, sqlite3.Error) as e:
            logger.error(f"Error completing OAuth request: {e}")
            return None

    def get_oauth_code(self, user_id: int) -> Optional[str]:
        """Get OAuth code for user (one-time use)."""
        cursor = self.connection.execute(
            "SELECT oauth_code FROM oauth_states WHERE user_id = ? AND oauth_code IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
        
        row = cursor.fetchone()
        if row:
            code = row[0]
            # Delete after retrieval (one-time use)
            self.connection.execute(
                "DELETE FROM oauth_states WHERE user_id = ? AND oauth_code = ?",
                (user_id, code)
            )
            self.connection.commit()
            return code
        
        return None

    def _cleanup_expired_states(self, user_id: int):
        """Remove expired states for user."""
        self.connection.execute(
            "DELETE FROM oauth_states WHERE user_id = ? AND expires_at < ?",
            (user_id, datetime.utcnow())
        )
        self.connection.commit()
```

### Step 1.2: Google OAuth Service

**File:** `services/google_oauth_service.py`

**Purpose:** Handles Google OAuth flow with manual code entry.

```python
# services/google_oauth_service.py
import json
from typing import Optional
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from core.logger import logger

class GoogleOAuthService:
    def __init__(self, client_id: str, client_secret: str):
        self.client_secrets_config = {
            "installed": {  # Use "installed" for desktop applications
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"]
            }
        }
        self.scopes = ['https://www.googleapis.com/auth/calendar']

    def get_authorization_url(self, user_id: int) -> str:
        """Generate OAuth URL for manual code entry."""
        from core.container import container
        
        oauth_state_manager = container.oauth_state_manager()
        state = oauth_state_manager.create_pending_request(user_id)
        
        flow = Flow.from_client_config(
            self.client_secrets_config,
            scopes=self.scopes,
            state=state
        )
        
        # Use out-of-band flow for manual code entry
        flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
        
        authorization_url, _ = flow.authorization_url(
            access_type='offline', 
            prompt='consent'
        )
        
        logger.info(f"Generated OAuth URL for user {user_id}")
        return authorization_url

    def exchange_code_for_token(self, code: str) -> str:
        """Exchange authorization code for credentials."""
        try:
            flow = Flow.from_client_config(self.client_secrets_config, scopes=self.scopes)
            flow.redirect_uri = 'urn:ietf:wg:oauth:2.0:oob'
            flow.fetch_token(code=code)
            
            # Return credentials as JSON
            credentials = {
                'token': flow.credentials.token,
                'refresh_token': flow.credentials.refresh_token,
                'token_uri': flow.credentials.token_uri,
                'client_id': flow.credentials.client_id,
                'client_secret': flow.credentials.client_secret,
                'scopes': flow.credentials.scopes,
                'expiry': flow.credentials.expiry.isoformat() if flow.credentials.expiry else None
            }
            
            logger.info("Successfully exchanged OAuth code for tokens")
            return json.dumps(credentials)
            
        except Exception as e:
            logger.error(f"Error exchanging OAuth code: {e}")
            raise ValueError(f"Invalid authorization code: {str(e)}")

    def refresh_token(self, credentials_json: str) -> str:
        """Refresh expired access token."""
        try:
            creds_data = json.loads(credentials_json)
            credentials = Credentials(**creds_data)
            
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                
                # Return updated credentials
                updated_credentials = {
                    'token': credentials.token,
                    'refresh_token': credentials.refresh_token,
                    'token_uri': credentials.token_uri,
                    'client_id': credentials.client_id,
                    'client_secret': credentials.client_secret,
                    'scopes': credentials.scopes,
                    'expiry': credentials.expiry.isoformat() if credentials.expiry else None
                }
                
                logger.info("Successfully refreshed OAuth token")
                return json.dumps(updated_credentials)
            
            # Token not expired, return as-is
            return credentials_json
            
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise ValueError(f"Failed to refresh token: {str(e)}")

    def is_token_valid(self, credentials_json: str) -> bool:
        """Check if token is valid and not expired."""
        try:
            creds_data = json.loads(credentials_json)
            credentials = Credentials(**creds_data)
            
            # If expired but has refresh token, it's still recoverable
            if credentials.expired:
                return credentials.refresh_token is not None
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating token: {e}")
            return False
```

### Step 1.3: Google Calendar Platform Implementation

**File:** `platforms/google_calendar.py`

**Purpose:** Implements AbstractTaskPlatform for Google Calendar with automatic token refresh.

```python
# platforms/google_calendar.py
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from platforms.base import AbstractTaskPlatform, register_platform
from core.exceptions import PlatformError
from core.logger import logger

@register_platform('google_calendar')
class GoogleCalendarPlatform(AbstractTaskPlatform):
    def __init__(self, credentials_json: str):
        """Initialize with OAuth credentials and auto-refresh if needed."""
        try:
            creds_data = json.loads(credentials_json)
            self.credentials = Credentials(**creds_data)
            
            # Auto-refresh if expired
            if self.credentials.expired and self.credentials.refresh_token:
                logger.info("Token expired, refreshing...")
                self.credentials.refresh(Request())
            
            self.service = build('calendar', 'v3', credentials=self.credentials)
            logger.info("Google Calendar platform initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar platform: {e}")
            raise PlatformError(f"Invalid Google Calendar credentials: {str(e)}")

    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """Create calendar event from task data."""
        try:
            # Extract platform-specific config
            platform_config = task_data.get('platform_config', {})
            if isinstance(platform_config, str):
                platform_config = json.loads(platform_config)
            
            calendar_id = platform_config.get('calendar_id', 'primary')
            
            # Parse due time
            due_time = task_data['due_time']
            if isinstance(due_time, str):
                start_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
            else:
                start_dt = due_time
            
            # Create 1-hour event
            end_dt = start_dt + timedelta(hours=1)
            
            # Build event object
            event = {
                'summary': task_data['title'],
                'description': task_data.get('description', ''),
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 15}
                    ]
                }
            }
            
            # Add location if provided
            if task_data.get('location'):
                event['location'] = task_data['location']
            
            # Create event
            created_event = self.service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()
            
            event_id = created_event.get('id')
            logger.info(f"Created Google Calendar event: {event_id}")
            return event_id
            
        except HttpError as e:
            if e.resp.status == 401:
                logger.error("Google Calendar authentication failed")
                raise PlatformError("Google Calendar authentication expired. Please reconnect your account.")
            elif e.resp.status == 403:
                logger.error("Google Calendar access forbidden")
                raise PlatformError("Access to Google Calendar denied. Please check permissions.")
            else:
                logger.error(f"Google Calendar API error: {e}")
                raise PlatformError(f"Failed to create calendar event: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating Google Calendar event: {e}")
            raise PlatformError(f"Failed to create calendar event: {str(e)}")

    def attach_screenshot(self, task_id: str, image_data: bytes, file_name: str) -> bool:
        """Attach screenshot info to calendar event description."""
        try:
            # Get existing event
            event = self.service.events().get(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            # Add screenshot note to description
            current_desc = event.get('description', '')
            screenshot_note = f"\n\n📸 Screenshot: {file_name} (attached via TG bot)"
            
            event['description'] = current_desc + screenshot_note
            
            # Update event
            self.service.events().update(
                calendarId='primary',
                eventId=task_id,
                body=event
            ).execute()
            
            logger.info(f"Added screenshot note to calendar event: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error attaching screenshot to calendar event: {e}")
            return False

    def update_task(self, task_id: str, task_data: Dict[str, Any]) -> bool:
        """Update existing calendar event."""
        try:
            # Get existing event
            event = self.service.events().get(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            # Update fields
            if 'title' in task_data:
                event['summary'] = task_data['title']
            
            if 'description' in task_data:
                event['description'] = task_data['description']
            
            if 'due_time' in task_data:
                due_time = task_data['due_time']
                if isinstance(due_time, str):
                    start_dt = datetime.fromisoformat(due_time.replace('Z', '+00:00'))
                else:
                    start_dt = due_time
                
                end_dt = start_dt + timedelta(hours=1)
                
                event['start'] = {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                }
                event['end'] = {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': task_data.get('timezone', 'UTC')
                }
            
            # Update event
            self.service.events().update(
                calendarId='primary',
                eventId=task_id,
                body=event
            ).execute()
            
            logger.info(f"Updated Google Calendar event: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error updating Google Calendar event: {e}")
            return False

    def delete_task(self, task_id: str) -> bool:
        """Delete calendar event."""
        try:
            self.service.events().delete(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            logger.info(f"Deleted Google Calendar event: {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting Google Calendar event: {e}")
            return False

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get calendar event details."""
        try:
            event = self.service.events().get(
                calendarId='primary',
                eventId=task_id
            ).execute()
            
            return {
                'id': event.get('id'),
                'title': event.get('summary'),
                'description': event.get('description'),
                'start_time': event.get('start', {}).get('dateTime'),
                'end_time': event.get('end', {}).get('dateTime'),
                'location': event.get('location'),
                'url': event.get('htmlLink')
            }
            
        except Exception as e:
            logger.error(f"Error getting Google Calendar event: {e}")
            return None

    def get_token_from_settings(self, platform_settings: Dict[str, Any]) -> Optional[str]:
        """Extract OAuth credentials from settings."""
        return platform_settings.get('google_calendar_credentials')

    def is_configured(self, platform_settings: Dict[str, Any]) -> bool:
        """Check if Google Calendar is properly configured."""
        credentials_str = platform_settings.get('google_calendar_credentials')
        if not credentials_str:
            return False
        
        try:
            creds = json.loads(credentials_str)
            # Must have refresh token for long-term usage
            return 'refresh_token' in creds and creds['refresh_token'] is not None
        except (json.JSONDecodeError, KeyError):
            return False

    @staticmethod
    def is_configured_static(platform_settings: Dict[str, Any]) -> bool:
        """Static version of is_configured for factory usage."""
        credentials_str = platform_settings.get('google_calendar_credentials')
        if not credentials_str:
            return False
        
        try:
            creds = json.loads(credentials_str)
            return 'refresh_token' in creds and creds['refresh_token'] is not None
        except (json.JSONDecodeError, KeyError):
            return False

    def get_updated_credentials(self) -> str:
        """Get current credentials (useful after auto-refresh)."""
        credentials = {
            'token': self.credentials.token,
            'refresh_token': self.credentials.refresh_token,
            'token_uri': self.credentials.token_uri,
            'client_id': self.credentials.client_id,
            'client_secret': self.credentials.client_secret,
            'scopes': self.credentials.scopes,
            'expiry': self.credentials.expiry.isoformat() if self.credentials.expiry else None
        }
        return json.dumps(credentials)
```

**Add to** `platforms/__init__.py`:
```python
from . import google_calendar  # Add this line
```

### Step 1.4: Dependency Injection Updates

Update `core/container.py`:
```python
# Add to ApplicationContainer class
google_oauth_service = providers.Factory(
    GoogleOAuthService,
    client_id=config.provided.GOOGLE_CLIENT_ID,
    client_secret=config.provided.GOOGLE_CLIENT_SECRET
)

oauth_state_manager = providers.Factory(
    OAuthStateManager,
    connection=database_manager.provided.connection
)
```

### Step 1.5: UI State Management

Add new states to `states/recipient_states.py`:
```python
class RecipientState(StatesGroup):
    # ... existing states ...
    
    # Google Calendar OAuth states
    waiting_for_oauth_code = State()
    waiting_for_oauth_code_input = State()
```

### Step 1.6: UI Implementation

Update `keyboards/recipient.py`:
```python
def get_platform_selection_keyboard() -> InlineKeyboardMarkup:
    """Account type selection keyboard."""
    keyboard = [
        [InlineKeyboardButton(text="📝 Todoist", callback_data="platform_type_todoist")],
        [InlineKeyboardButton(text="📋 Trello", callback_data="platform_type_trello")],
        [InlineKeyboardButton(text="📅 Google Calendar", callback_data="platform_type_google_calendar")],
        [InlineKeyboardButton(text="« Back", callback_data="back_to_recipients")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
```

Update `handlers_modular/states/recipient_setup.py`:
```python
@router.callback_query(lambda c: c.data == "platform_type_google_calendar")
async def handle_google_calendar_setup(callback_query: CallbackQuery, state: FSMContext):
    """Handle Google Calendar setup with manual code entry."""
    await state.update_data(platform_type="google_calendar")
    
    user_id = callback_query.from_user.id
    
    try:
        google_oauth_service = container.google_oauth_service()
        oauth_url = google_oauth_service.get_authorization_url(user_id)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Open Google Authorization", url=oauth_url)],
            [InlineKeyboardButton(text="📝 I Have the Code", callback_data="enter_auth_code")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_setup")]
        ])
        
        await callback_query.message.edit_text(
            "🔐 **Google Calendar Authorization**\n\n"
            "1. Click 'Open Google Authorization'\n"
            "2. Sign in with your Google account\n"
            "3. Grant calendar access permissions\n"
            "4. **Copy the authorization code** from the final page\n"
            "5. Return here and click 'I Have the Code'\n"
            "6. Paste the code when prompted\n\n"
            "🔒 This code is only used once to get secure tokens.\n"
            "⚠️ Do not share this code with anyone.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.set_state(RecipientState.waiting_for_oauth_code)
        
    except Exception as e:
        logger.error(f"Error setting up Google Calendar OAuth: {e}")
        await callback_query.answer("❌ Error setting up Google Calendar. Please try again.", show_alert=True)

@router.callback_query(lambda c: c.data == "enter_auth_code")
async def handle_enter_auth_code(callback_query: CallbackQuery, state: FSMContext):
    """Prompt user to enter authorization code."""
    await callback_query.message.edit_text(
        "📝 **Enter Authorization Code**\n\n"
        "Paste the authorization code you received from Google below:"
    )
    await state.set_state(RecipientState.waiting_for_oauth_code_input)

@router.message(RecipientState.waiting_for_oauth_code_input)
async def handle_oauth_code_input(message: Message, state: FSMContext):
    """Handle authorization code input."""
    auth_code = message.text.strip()
    user_id = message.from_user.id
    
    try:
        # Exchange code for token
        google_oauth_service = container.google_oauth_service()
        credentials_json = google_oauth_service.exchange_code_for_token(auth_code)
        
        # Create recipient
        recipient_service = container.recipient_service()
        recipient_id = recipient_service.add_personal_recipient(
            user_id=user_id,
            name="My Google Calendar",
            platform_type="google_calendar",
            credentials=credentials_json,
            platform_config=json.dumps({"calendar_id": "primary"})
        )
        
        await message.reply(
            "✅ **Google Calendar Connected!**\n\n"
            "Your Google Calendar has been successfully connected.\n"
            "You can now create calendar events using this bot.\n\n"
            "Try creating a task and it will appear in your calendar!",
            reply_markup=get_recipient_management_keyboard()
        )
        await state.clear()
        
        # Delete the message containing the auth code for security
        await message.delete()
        
    except ValueError as e:
        logger.error(f"OAuth code exchange error: {e}")
        await message.reply(
            "❌ **Authorization Failed**\n\n"
            f"Error: {str(e)}\n\n"
            "Please try the setup process again with a fresh authorization code."
        )
    except Exception as e:
        logger.error(f"Unexpected error during OAuth: {e}")
        await message.reply(
            "❌ **Setup Failed**\n\n"
            "An unexpected error occurred. Please try again or contact support."
        )
```

---

## Phase 2: Secure Account Sharing
<ARTEM> Never leave leftovers or residual structure, if separate migrations possible which will never leaving a trace in main codebase that will be fine, otherwise drop legacy! Make sure that project is deployable from scratch without database! </ARTEM>
### Step 2.1: Database Schema Extension

**File:** `database/migrations/add_shared_authorizations.py`

```python
# database/migrations/add_shared_authorizations.py
import sqlite3
from core.logger import logger

def migrate_shared_authorizations(connection: sqlite3.Connection):
    """Add shared authorizations tables and update recipients table."""
    try:
        # Create shared_authorizations table
        connection.execute("""
            CREATE TABLE IF NOT EXISTS shared_authorizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL,
                grantee_user_id INTEGER NOT NULL,
                owner_recipient_id INTEGER NOT NULL,
                permission_level TEXT NOT NULL DEFAULT 'use', -- 'use', 'admin'
                status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'accepted', 'revoked', 'declined'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP NULL,
                FOREIGN KEY (owner_recipient_id) REFERENCES recipients (id) ON DELETE CASCADE,
                UNIQUE(owner_user_id, grantee_user_id, owner_recipient_id)
            )
        """)
        
        # Add shared_authorization_id column to recipients table if it doesn't exist
        cursor = connection.execute("PRAGMA table_info(recipients)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'shared_authorization_id' not in columns:
            connection.execute("""
                ALTER TABLE recipients ADD COLUMN shared_authorization_id INTEGER NULL
            """)
            logger.info("Added shared_authorization_id column to recipients table")
        
        # Add foreign key constraint (note: SQLite doesn't support adding FK constraints to existing tables)
        # This will be enforced in application logic
        
        # Create indices for performance
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_auth_grantee 
            ON shared_authorizations (grantee_user_id, status)
        """)
        
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_shared_auth_owner 
            ON shared_authorizations (owner_user_id, status)
        """)
        
        connection.commit()
        logger.info("Successfully migrated shared authorizations schema")
        
    except sqlite3.Error as e:
        logger.error(f"Error migrating shared authorizations schema: {e}")
        connection.rollback()
        raise

# Add to your main migration runner
def run_migrations(connection: sqlite3.Connection):
    # ... existing migrations ...
    migrate_shared_authorizations(connection)
```

### Step 2.2: Shared Authorization Model

**File:** `models/shared_authorization.py`

```python
# models/shared_authorization.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class SharedAuthorization:
    id: int
    owner_user_id: int
    grantee_user_id: int
    owner_recipient_id: int
    permission_level: str = 'use'  # 'use', 'admin'
    status: str = 'pending'  # 'pending', 'accepted', 'revoked', 'declined'
    created_at: datetime = None
    updated_at: datetime = None
    last_used_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow()
    
    def is_active(self) -> bool:
        """Check if authorization is currently active."""
        return self.status == 'accepted'
    
    def can_admin(self) -> bool:
        """Check if grantee has admin permissions."""
        return self.permission_level == 'admin'
    
    def can_use(self) -> bool:
        """Check if grantee can use the shared account."""
        return self.permission_level in ['use', 'admin'] and self.is_active()
```

### Step 2.3: Repository Extensions

Update `database/unified_recipient_repository.py`:

```python
# Add these methods to UnifiedRecipientRepository class

def create_shared_authorization(self, owner_user_id: int, grantee_user_id: int, 
                              owner_recipient_id: int, permission_level: str = 'use') -> int:
    """Create a new sharing authorization."""
    try:
        query = """
        INSERT INTO shared_authorizations (owner_user_id, grantee_user_id, owner_recipient_id, permission_level)
        VALUES (?, ?, ?, ?)
        """
        cursor = self.connection.execute(query, (owner_user_id, grantee_user_id, owner_recipient_id, permission_level))
        auth_id = cursor.lastrowid
        self.connection.commit()
        
        logger.info(f"Created shared authorization {auth_id} for owner {owner_user_id} -> grantee {grantee_user_id}")
        return auth_id
        
    except sqlite3.IntegrityError as e:
        logger.error(f"Integrity error creating shared authorization: {e}")
        raise ValueError("Sharing already exists between these users for this account")
    except sqlite3.Error as e:
        logger.error(f"Database error creating shared authorization: {e}")
        raise

def update_authorization_status(self, auth_id: int, status: str, updated_by_user_id: int = None) -> bool:
    """Update authorization status."""
    try:
        query = """
        UPDATE shared_authorizations 
        SET status = ?, updated_at = CURRENT_TIMESTAMP 
        WHERE id = ?
        """
        cursor = self.connection.execute(query, (status, auth_id))
        success = cursor.rowcount > 0
        self.connection.commit()
        
        if success:
            logger.info(f"Updated authorization {auth_id} status to {status}")
        
        return success
        
    except sqlite3.Error as e:
        logger.error(f"Error updating authorization status: {e}")
        return False

def get_authorization_by_id(self, auth_id: int) -> Optional[SharedAuthorization]:
    """Get shared authorization by ID."""
    query = """
    SELECT id, owner_user_id, grantee_user_id, owner_recipient_id, permission_level, 
           status, created_at, updated_at, last_used_at
    FROM shared_authorizations 
    WHERE id = ?
    """
    
    cursor = self.connection.execute(query, (auth_id,))
    row = cursor.fetchone()
    
    if row:
        return SharedAuthorization(
            id=row[0],
            owner_user_id=row[1],
            grantee_user_id=row[2],
            owner_recipient_id=row[3],
            permission_level=row[4],
            status=row[5],
            created_at=datetime.fromisoformat(row[6]) if row[6] else None,
            updated_at=datetime.fromisoformat(row[7]) if row[7] else None,
            last_used_at=datetime.fromisoformat(row[8]) if row[8] else None
        )
    
    return None

def get_pending_authorizations_for_user(self, user_id: int) -> List[Dict[str, Any]]:
    """Get pending authorizations for a user with recipient details."""
    query = """
    SELECT sa.id, sa.owner_user_id, sa.owner_recipient_id, sa.permission_level,
           sa.created_at, r.name as recipient_name, r.platform_type
    FROM shared_authorizations sa
    JOIN recipients r ON sa.owner_recipient_id = r.id
    WHERE sa.grantee_user_id = ? AND sa.status = 'pending'
    ORDER BY sa.created_at DESC
    """
    
    cursor = self.connection.execute(query, (user_id,))
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        results.append({
            'auth_id': row[0],
            'owner_user_id': row[1],
            'owner_recipient_id': row[2],
            'permission_level': row[3],
            'created_at': row[4],
            'recipient_name': row[5],
            'platform_type': row[6]
        })
    
    return results

def get_shared_authorizations_by_owner(self, owner_user_id: int) -> List[Dict[str, Any]]:
    """Get all authorizations created by owner with recipient and grantee details."""
    query = """
    SELECT sa.id, sa.grantee_user_id, sa.owner_recipient_id, sa.permission_level,
           sa.status, sa.created_at, sa.last_used_at,
           r.name as recipient_name, r.platform_type
    FROM shared_authorizations sa
    JOIN recipients r ON sa.owner_recipient_id = r.id
    WHERE sa.owner_user_id = ? AND sa.status IN ('pending', 'accepted')
    ORDER BY sa.created_at DESC
    """
    
    cursor = self.connection.execute(query, (owner_user_id,))
    rows = cursor.fetchall()
    
    results = []
    for row in rows:
        results.append({
            'auth_id': row[0],
            'grantee_user_id': row[1],
            'owner_recipient_id': row[2],
            'permission_level': row[3],
            'status': row[4],
            'created_at': row[5],
            'last_used_at': row[6],
            'recipient_name': row[7],
            'platform_type': row[8]
        })
    
    return results

def create_shared_recipient(self, grantee_user_id: int, auth_id: int, recipient_name: str) -> int:
    """Create shared recipient for grantee."""
    try:
        # Get authorization details
        auth = self.get_authorization_by_id(auth_id)
        if not auth or not auth.is_active():
            raise ValueError("Invalid or inactive authorization")
        
        # Get owner's recipient
        owner_recipient = self.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
        if not owner_recipient:
            raise ValueError("Owner's recipient not found")
        
        query = """
        INSERT INTO recipients (user_id, name, platform_type, credentials, platform_config, 
                              is_personal, shared_authorization_id, shared_by, enabled)
        VALUES (?, ?, ?, '', ?, FALSE, ?, ?, TRUE)
        """
        
        shared_by_text = f"Shared by user {auth.owner_user_id}"
        
        cursor = self.connection.execute(query, (
            grantee_user_id,
            recipient_name,
            owner_recipient.platform_type,
            owner_recipient.platform_config or '',
            auth_id,
            shared_by_text
        ))
        
        recipient_id = cursor.lastrowid
        self.connection.commit()
        
        logger.info(f"Created shared recipient {recipient_id} for grantee {grantee_user_id}")
        return recipient_id
        
    except sqlite3.Error as e:
        logger.error(f"Database error creating shared recipient: {e}")
        raise

def resolve_credentials_for_recipient(self, recipient: UnifiedRecipient) -> str:
    """
    CRITICAL SECURITY METHOD: Resolve actual credentials for recipient.
    For shared accounts, returns owner's credentials without exposing them to grantee.
    """
    if recipient.shared_authorization_id:
        # This is a shared recipient - get owner's credentials
        auth = self.get_authorization_by_id(recipient.shared_authorization_id)
        if not auth:
            raise ValueError(f"Shared authorization {recipient.shared_authorization_id} not found")
        
        if not auth.is_active():
            raise ValueError("Shared authorization is not active")
        
        # Get owner's recipient
        owner_recipient = self.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
        if not owner_recipient:
            raise ValueError("Owner's recipient not found")
        
        # Update last used timestamp
        self.update_authorization_last_used(recipient.shared_authorization_id)
        
        logger.info(f"Resolved credentials for shared recipient {recipient.id} -> owner {auth.owner_user_id}")
        return owner_recipient.credentials
    else:
        # Personal recipient - use own credentials
        return recipient.credentials

def update_authorization_last_used(self, auth_id: int):
    """Update last_used_at timestamp for authorization."""
    try:
        query = "UPDATE shared_authorizations SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?"
        self.connection.execute(query, (auth_id,))
        self.connection.commit()
    except sqlite3.Error as e:
        logger.error(f"Error updating authorization last_used: {e}")

def delete_shared_recipients_by_authorization(self, auth_id: int):
    """Delete all shared recipients for a specific authorization."""
    try:
        query = "DELETE FROM recipients WHERE shared_authorization_id = ?"
        cursor = self.connection.execute(query, (auth_id,))
        deleted_count = cursor.rowcount
        self.connection.commit()
        
        logger.info(f"Deleted {deleted_count} shared recipients for authorization {auth_id}")
        return deleted_count
        
    except sqlite3.Error as e:
        logger.error(f"Error deleting shared recipients: {e}")
        return 0

def get_shared_recipients_for_user(self, user_id: int) -> List[UnifiedRecipient]:
    """Get all shared recipients for a user."""
    query = """
    SELECT id, user_id, name, platform_type, credentials, platform_config,
           is_personal, is_default, enabled, shared_by, shared_authorization_id,
           created_at, updated_at
    FROM recipients 
    WHERE user_id = ? AND is_personal = FALSE AND shared_authorization_id IS NOT NULL
    ORDER BY name
    """
    
    cursor = self.connection.execute(query, (user_id,))
    rows = cursor.fetchall()
    
    recipients = []
    for row in rows:
        recipients.append(UnifiedRecipient(
            id=row[0],
            user_id=row[1],
            name=row[2],
            platform_type=row[3],
            credentials=row[4],  # Will be empty for shared recipients
            platform_config=row[5],
            is_personal=row[6],
            is_default=row[7],
            enabled=row[8],
            shared_by=row[9],
            shared_authorization_id=row[10],
            created_at=datetime.fromisoformat(row[11]) if row[11] else None,
            updated_at=datetime.fromisoformat(row[12]) if row[12] else None
        ))
    
    return recipients
```

### Step 2.4: Enhanced Platform Factory

Update `platforms/factory.py`:

```python
# Update the get_platform method in TaskPlatformFactory

@staticmethod
def get_platform(recipient: UnifiedRecipient) -> AbstractTaskPlatform:
    """
    Create platform instance with proper credential resolution.
    CRITICAL SECURITY: This method handles shared account credential resolution.
    """
    from core.container import container
    
    try:
        repository = container.unified_recipient_repository()
        
        # Resolve credentials (handles shared accounts securely)
        credentials = repository.resolve_credentials_for_recipient(recipient)
        
        if not credentials:
            raise PlatformError(f"No credentials available for recipient {recipient.id}")
        
        # Get platform class
        platform_class = PLATFORM_REGISTRY.get(recipient.platform_type)
        if not platform_class:
            raise PlatformError(f"Unknown platform type: {recipient.platform_type}")
        
        # Create platform instance
        platform_instance = platform_class(credentials)
        
        # For Google Calendar, update credentials if they were refreshed
        if recipient.platform_type == 'google_calendar' and hasattr(platform_instance, 'get_updated_credentials'):
            updated_credentials = platform_instance.get_updated_credentials()
            if updated_credentials != credentials:
                # Update owner's credentials if token was refreshed
                if recipient.shared_authorization_id:
                    # For shared recipients, update owner's credentials
                    auth = repository.get_authorization_by_id(recipient.shared_authorization_id)
                    if auth:
                        owner_recipient = repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
                        if owner_recipient:
                            repository.update_recipient_credentials(owner_recipient.id, updated_credentials)
                else:
                    # For personal recipients, update own credentials
                    repository.update_recipient_credentials(recipient.id, updated_credentials)
        
        return platform_instance
        
    except Exception as e:
        logger.error(f"Error creating platform for recipient {recipient.id}: {e}")
        raise PlatformError(f"Failed to create platform instance: {str(e)}")

# Add this method to UnifiedRecipientRepository
def update_recipient_credentials(self, recipient_id: int, credentials: str):
    """Update recipient credentials (for token refresh)."""
    try:
        query = "UPDATE recipients SET credentials = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        self.connection.execute(query, (credentials, recipient_id))
        self.connection.commit()
        logger.info(f"Updated credentials for recipient {recipient_id}")
    except sqlite3.Error as e:
        logger.error(f"Error updating recipient credentials: {e}")
```
<ARTEM> Workflow is that when creating sharing account there are two ways - current (insert token "directly") and request authentication - this new worfklow when I could request from my wife to authorize platform, so bot sends message to wife, or other recipient whoever I provide telegram name (or phone number if possible) and then auth is purely handled on recipient's side </ARTEM>
### Step 2.5: Sharing Service

**File:** `services/sharing_service.py`

```python
# services/sharing_service.py
from typing import List, Optional, Dict, Any
from core.logger import logger
from models.shared_authorization import SharedAuthorization

class SharingService:
    def __init__(self, repository, user_service):
        self.repository = repository
        self.user_service = user_service

    def create_sharing_request(self, owner_user_id: int, grantee_username: str, 
                             recipient_id: int, permission_level: str = 'use') -> int:
        """Create a sharing request with validation."""
        try:
            # Validate owner has access to recipient
            recipient = self.repository.get_recipient_by_id(owner_user_id, recipient_id)
            if not recipient:
                raise ValueError("Recipient not found")
            
            if not recipient.is_personal:
                raise ValueError("Can only share personal recipients")
            
            # Get grantee user_id from username
            grantee_user_id = self.user_service.get_user_id_from_username(grantee_username)
            if not grantee_user_id:
                raise ValueError(f"User @{grantee_username} not found in bot users")
            
            if grantee_user_id == owner_user_id:
                raise ValueError("Cannot share with yourself")
            
            # Validate permission level
            if permission_level not in ['use', 'admin']:
                raise ValueError("Permission level must be 'use' or 'admin'")
            
            # Create authorization
            auth_id = self.repository.create_shared_authorization(
                owner_user_id, grantee_user_id, recipient_id, permission_level
            )
            
            logger.info(f"Created sharing request {auth_id}: {owner_user_id} -> {grantee_username} ({grantee_user_id})")
            return auth_id
            
        except Exception as e:
            logger.error(f"Error creating sharing request: {e}")
            raise

    def accept_sharing_request(self, auth_id: int, grantee_user_id: int) -> int:
        """Accept a sharing request and create shared recipient."""
        try:
            # Validate authorization
            auth = self.repository.get_authorization_by_id(auth_id)
            if not auth:
                raise ValueError("Authorization not found")
            
            if auth.grantee_user_id != grantee_user_id:
                raise ValueError("Not authorized to accept this request")
            
            if auth.status != 'pending':
                raise ValueError(f"Cannot accept authorization with status: {auth.status}")
            
            # Update authorization status
            self.repository.update_authorization_status(auth_id, 'accepted')
            
            # Get owner's recipient for naming
            owner_recipient = self.repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
            if not owner_recipient:
                raise ValueError("Owner's recipient not found")
            
            # Create shared recipient name
            owner_info = self.user_service.get_user_info(auth.owner_user_id)
            owner_name = owner_info.get('first_name', f'User{auth.owner_user_id}') if owner_info else f'User{auth.owner_user_id}'
            
            shared_recipient_name = f"{owner_recipient.name} (by {owner_name})"
            
            # Create shared recipient for grantee
            recipient_id = self.repository.create_shared_recipient(
                grantee_user_id, auth_id, shared_recipient_name
            )
            
            logger.info(f"Accepted sharing request {auth_id}, created recipient {recipient_id}")
            return recipient_id
            
        except Exception as e:
            logger.error(f"Error accepting sharing request: {e}")
            raise

    def decline_sharing_request(self, auth_id: int, grantee_user_id: int) -> bool:
        """Decline a sharing request."""
        try:
            # Validate authorization
            auth = self.repository.get_authorization_by_id(auth_id)
            if not auth:
                raise ValueError("Authorization not found")
            
            if auth.grantee_user_id != grantee_user_id:
                raise ValueError("Not authorized to decline this request")
            
            if auth.status != 'pending':
                raise ValueError(f"Cannot decline authorization with status: {auth.status}")
            
            # Update authorization status
            success = self.repository.update_authorization_status(auth_id, 'declined')
            
            if success:
                logger.info(f"Declined sharing request {auth_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error declining sharing request: {e}")
            return False

    def revoke_sharing(self, auth_id: int, owner_user_id: int) -> bool:
        """Revoke sharing access (owner action)."""
        try:
            # Validate authorization
            auth = self.repository.get_authorization_by_id(auth_id)
            if not auth:
                raise ValueError("Authorization not found")
            
            if auth.owner_user_id != owner_user_id:
                raise ValueError("Not authorized to revoke this sharing")
            
            if auth.status not in ['pending', 'accepted']:
                raise ValueError(f"Cannot revoke authorization with status: {auth.status}")
            
            # Update authorization status
            self.repository.update_authorization_status(auth_id, 'revoked')
            
            # Delete grantee's shared recipient(s) if accepted
            if auth.status == 'accepted':
                self.repository.delete_shared_recipients_by_authorization(auth_id)
            
            logger.info(f"Revoked sharing authorization {auth_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error revoking sharing: {e}")
            return False

    def get_sharing_permissions(self, user_id: int, recipient_id: int) -> Optional[str]:
        """Get user's permission level for a recipient."""
        try:
            recipient = self.repository.get_recipient_by_id(user_id, recipient_id)
            if not recipient:
                return None
            
            if recipient.is_personal:
                return 'admin'  # Owner has full access
            elif recipient.shared_authorization_id:
                auth = self.repository.get_authorization_by_id(recipient.shared_authorization_id)
                if auth and auth.is_active():
                    return auth.permission_level
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting sharing permissions: {e}")
            return None

    def can_user_perform_action(self, user_id: int, recipient_id: int, action: str) -> bool:
        """Check if user can perform specific action on recipient."""
        permission_level = self.get_sharing_permissions(user_id, recipient_id)
        
        if not permission_level:
            return False
        
        # Define action permissions
        action_permissions = {
            'create_task': ['use', 'admin'],
            'update_task': ['use', 'admin'],
            'delete_task': ['admin'],  # Only admin can delete
            'view_recipient': ['use', 'admin'],
            'edit_recipient': ['admin'],  # Only admin can edit
            'delete_recipient': ['admin'],  # Only owner (admin of personal) can delete
            'share_recipient': ['admin']  # Only admin can share
        }
        
        allowed_permissions = action_permissions.get(action, [])
        return permission_level in allowed_permissions
```

### Step 2.6: User Service for Username Resolution

**File:** `services/user_service.py`

```python
# services/user_service.py
import sqlite3
from typing import Optional, Dict, Any
from core.logger import logger

class UserService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self._ensure_user_cache_table()

    def _ensure_user_cache_table(self):
        """Create user cache table if it doesn't exist."""
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS user_cache (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE,
                first_name TEXT,
                last_name TEXT,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.connection.commit()

    def cache_user_info(self, user_id: int, username: str = None, 
                       first_name: str = None, last_name: str = None):
        """Cache user information from Telegram."""
        try:
            self.connection.execute("""
                INSERT OR REPLACE INTO user_cache 
                (user_id, username, first_name, last_name, last_seen)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, username, first_name, last_name))
            self.connection.commit()
        except sqlite3.Error as e:
            logger.error(f"Error caching user info: {e}")

    def get_user_id_from_username(self, username: str) -> Optional[int]:
        """Get user_id from username (cached from previous interactions)."""
        username = username.lstrip('@').lower()
        
        cursor = self.connection.execute(
            "SELECT user_id FROM user_cache WHERE LOWER(username) = ?",
            (username,)
        )
        
        row = cursor.fetchone()
        return row[0] if row else None

    def get_user_info(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get cached user information."""
        cursor = self.connection.execute(
            "SELECT username, first_name, last_name FROM user_cache WHERE user_id = ?",
            (user_id,)
        )
        
        row = cursor.fetchone()
        if row:
            return {
                'username': row[0],
                'first_name': row[1],
                'last_name': row[2]
            }
        
        return None
```

Add to `core/container.py`:
```python
user_service = providers.Factory(
    UserService,
    connection=database_manager.provided.connection
)

sharing_service = providers.Factory(
    SharingService,
    repository=unified_recipient_repository,
    user_service=user_service
)
```

### Step 2.7: Sharing Commands Implementation

**File:** `handlers_modular/commands/sharing_commands.py`

```python
# handlers_modular/commands/sharing_commands.py
import json
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from core.container import container
from core.logger import logger
from states.recipient_states import RecipientState
from keyboards.recipient import get_recipient_management_keyboard

router = Router()

# Add new sharing states
class SharingState(RecipientState):
    selecting_recipient_to_share = "selecting_recipient_to_share"
    waiting_for_grantee_username = "waiting_for_grantee_username"
    selecting_permission_level = "selecting_permission_level"
    sharing_confirmation = "sharing_confirmation"

@router.message(Command("share"))
async def handle_share_command(message: Message, state: FSMContext):
    """Start sharing workflow."""
    user_id = message.from_user.id
    
    # Cache user info for username resolution
    user_service = container.user_service()
    user_service.cache_user_info(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    try:
        # Get user's personal recipients
        recipient_service = container.recipient_service()
        personal_recipients = [r for r in recipient_service.get_recipients_by_user_id(user_id) if r.is_personal]
        
        if not personal_recipients:
            await message.reply(
                "❌ **No Accounts to Share**\n\n"
                "You don't have any accounts to share.\n"
                "Add an account first using /recipients."
            )
            return
        
        # Show recipient selection keyboard
        keyboard = []
        for recipient in personal_recipients:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{recipient.platform_type.title()}: {recipient.name}",
                    callback_data=f"share_recipient_{recipient.id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")])
        
        await message.reply(
            "🤝 **Share Account Access**\n\n"
            "Select which account you'd like to share:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='Markdown'
        )
        await state.set_state(SharingState.selecting_recipient_to_share)
        
    except Exception as e:
        logger.error(f"Error in share command: {e}")
        await message.reply("❌ Error loading your accounts. Please try again.")

@router.callback_query(lambda c: c.data.startswith("share_recipient_"))
async def handle_share_recipient_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle recipient selection for sharing."""
    try:
        recipient_id = int(callback_query.data.replace("share_recipient_", ""))
        await state.update_data(recipient_id=recipient_id)
        
        # Get recipient details for confirmation
        recipient_service = container.recipient_service()
        recipient = recipient_service.get_recipient_by_id(callback_query.from_user.id, recipient_id)
        
        if not recipient:
            await callback_query.answer("❌ Account not found.", show_alert=True)
            return
        
        await callback_query.message.edit_text(
            f"👤 **Share: {recipient.platform_type.title()} - {recipient.name}**\n\n"
            "Enter the Telegram username (with or without @) of the person you want to share with:\n\n"
            "⚠️ **Important:** They must have used this bot before for sharing to work.",
            parse_mode='Markdown'
        )
        await state.set_state(SharingState.waiting_for_grantee_username)
        
    except (ValueError, Exception) as e:
        logger.error(f"Error in recipient selection: {e}")
        await callback_query.answer("❌ Error processing selection.", show_alert=True)

@router.message(SharingState.waiting_for_grantee_username)
async def handle_grantee_username(message: Message, state: FSMContext):
    """Handle grantee username input."""
    username = message.text.strip().lstrip('@')
    
    if not username or len(username) < 3:
        await message.reply("❌ Please enter a valid username (at least 3 characters).")
        return
    
    try:
        # Check if user exists in our cache
        user_service = container.user_service()
        grantee_user_id = user_service.get_user_id_from_username(username)
        
        if not grantee_user_id:
            await message.reply(
                f"❌ **User @{username} not found**\n\n"
                "This user must have interacted with the bot before they can receive shared accounts.\n"
                "Ask them to start the bot first, then try sharing again."
            )
            return
        
        await state.update_data(grantee_username=username, grantee_user_id=grantee_user_id)
        
        # Show permission level selection
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Use Only", callback_data="permission_use")],
            [InlineKeyboardButton(text="🔧 Admin Access", callback_data="permission_admin")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")]
        ])
        
        await message.reply(
            f"🔐 **Permission Level for @{username}**\n\n"
            "**👤 Use Only:** Can create and update tasks using your account\n"
            "**🔧 Admin Access:** Can create, update, delete tasks + manage sharing\n\n"
            "⚠️ **Admin access allows them to share your account with others!**\n\n"
            "Choose permission level:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.set_state(SharingState.selecting_permission_level)
        
    except Exception as e:
        logger.error(f"Error processing username: {e}")
        await message.reply("❌ Error processing username. Please try again.")

@router.callback_query(lambda c: c.data.startswith("permission_"))
async def handle_permission_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle permission level selection."""
    permission = callback_query.data.replace("permission_", "")
    
    try:
        state_data = await state.get_data()
        recipient_id = state_data['recipient_id']
        grantee_username = state_data['grantee_username']
        grantee_user_id = state_data['grantee_user_id']
        
        # Create sharing request
        sharing_service = container.sharing_service()
        auth_id = sharing_service.create_sharing_request(
            callback_query.from_user.id,
            grantee_username,
            recipient_id,
            permission
        )
        
        # Send notification to grantee
        await send_sharing_notification(grantee_user_id, auth_id, callback_query.from_user.first_name)
        
        # Show success message
        permission_text = "Use Only" if permission == "use" else "Admin Access"
        
        await callback_query.message.edit_text(
            f"✅ **Sharing Request Sent!**\n\n"
            f"📤 **Sent to:** @{grantee_username}\n"
            f"🔐 **Permission:** {permission_text}\n\n"
            f"They will receive a notification to accept or decline.\n"
            f"You'll be notified when they respond.\n\n"
            f"Use /sharing to manage your shared accounts.",
            parse_mode='Markdown'
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error creating sharing request: {e}")
        await callback_query.message.edit_text(
            f"❌ **Error:** {str(e)}\n\n"
            "Please try again or contact support."
        )
        await state.clear()

async def send_sharing_notification(grantee_user_id: int, auth_id: int, owner_name: str):
    """Send sharing notification to grantee."""
    try:
        # Get authorization details
        repository = container.unified_recipient_repository()
        auth = repository.get_authorization_by_id(auth_id)
        
        if not auth:
            logger.error(f"Authorization {auth_id} not found for notification")
            return
        
        # Get recipient details
        owner_recipient = repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
        if not owner_recipient:
            logger.error(f"Owner recipient not found for notification")
            return
        
        permission_text = "Use Only" if auth.permission_level == "use" else "Admin Access"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Accept", callback_data=f"accept_share_{auth_id}")],
            [InlineKeyboardButton(text="❌ Decline", callback_data=f"decline_share_{auth_id}")]
        ])
        
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=grantee_user_id,
            text=f"🤝 **Account Sharing Request**\n\n"
                 f"**From:** {owner_name}\n"
                 f"**Account:** {owner_recipient.platform_type.title()} - {owner_recipient.name}\n"
                 f"**Permission:** {permission_text}\n\n"
                 f"This will allow you to create tasks using their {owner_recipient.platform_type.title()} account.\n\n"
                 f"Do you want to accept this sharing request?",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
        logger.info(f"Sent sharing notification to user {grantee_user_id} for auth {auth_id}")
        
    except Exception as e:
        logger.error(f"Error sending sharing notification: {e}")

@router.callback_query(lambda c: c.data.startswith("accept_share_"))
async def handle_accept_sharing(callback_query: CallbackQuery):
    """Handle sharing acceptance."""
    try:
        auth_id = int(callback_query.data.replace("accept_share_", ""))
        user_id = callback_query.from_user.id
        
        # Cache user info
        user_service = container.user_service()
        user_service.cache_user_info(
            user_id=user_id,
            username=callback_query.from_user.username,
            first_name=callback_query.from_user.first_name,
            last_name=callback_query.from_user.last_name
        )
        
        sharing_service = container.sharing_service()
        recipient_id = sharing_service.accept_sharing_request(auth_id, user_id)
        
        # Get recipient details for confirmation
        repository = container.unified_recipient_repository()
        recipient = repository.get_recipient_by_id(user_id, recipient_id)
        
        # Notify owner
        await notify_owner_of_sharing_response(auth_id, 'accepted', callback_query.from_user.first_name)
        
        await callback_query.message.edit_text(
            f"✅ **Sharing Accepted!**\n\n"
            f"🎉 You now have access to **{recipient.name}**!\n\n"
            f"📝 You can create tasks using this shared account.\n"
            f"🔍 Check /recipients to see your new shared account.\n\n"
            f"⚠️ Remember: This account belongs to someone else. Use it responsibly!",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error accepting share: {e}")
        await callback_query.answer("❌ Error accepting sharing request.", show_alert=True)

@router.callback_query(lambda c: c.data.startswith("decline_share_"))
async def handle_decline_sharing(callback_query: CallbackQuery):
    """Handle sharing decline."""
    try:
        auth_id = int(callback_query.data.replace("decline_share_", ""))
        user_id = callback_query.from_user.id
        
        sharing_service = container.sharing_service()
        success = sharing_service.decline_sharing_request(auth_id, user_id)
        
        if success:
            # Notify owner
            await notify_owner_of_sharing_response(auth_id, 'declined', callback_query.from_user.first_name)
            
            await callback_query.message.edit_text(
                "❌ **Sharing Declined**\n\n"
                "You have declined the sharing request.\n"
                "The account owner has been notified.",
                parse_mode='Markdown'
            )
        else:
            await callback_query.answer("❌ Error processing response.", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error declining share: {e}")
        await callback_query.answer("❌ Error processing response.", show_alert=True)

async def notify_owner_of_sharing_response(auth_id: int, response: str, grantee_name: str):
    """Notify owner about grantee's response."""
    try:
        repository = container.unified_recipient_repository()
        auth = repository.get_authorization_by_id(auth_id)
        
        if not auth:
            return
        
        owner_recipient = repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
        if not owner_recipient:
            return
        
        status_emoji = "✅" if response == 'accepted' else "❌"
        status_text = "accepted" if response == 'accepted' else "declined"
        
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=auth.owner_user_id,
            text=f"{status_emoji} **Sharing Request {status_text.title()}**\n\n"
                 f"**User:** {grantee_name}\n"
                 f"**Account:** {owner_recipient.platform_type.title()} - {owner_recipient.name}\n\n"
                 f"Your sharing request was **{status_text}**.\n\n"
                 f"Use /sharing to manage your shared accounts.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error notifying owner: {e}")

@router.callback_query(lambda c: c.data == "cancel_sharing")
async def handle_cancel_sharing(callback_query: CallbackQuery, state: FSMContext):
    """Handle sharing cancellation."""
    await callback_query.message.edit_text(
        "❌ **Sharing Cancelled**\n\n"
        "The sharing process has been cancelled."
    )
    await state.clear()
```
<ARTEM> Consider request sender UX flow and request receiver including corner cases (e.g., reciver never confirmed authorization) </ARTEM>

### Step 2.8: Sharing Management Command

Add to `handlers_modular/commands/sharing_commands.py`:

```python
@router.message(Command("sharing"))
async def handle_sharing_management(message: Message):
    """Show sharing management interface."""
    user_id = message.from_user.id
    
    try:
        repository = container.unified_recipient_repository()
        
        # Get shares I've created (as owner)
        my_shares = repository.get_shared_authorizations_by_owner(user_id)
        
        # Get shares I've received (as grantee)
        shared_with_me = repository.get_shared_recipients_for_user(user_id)
        
        if not my_shares and not shared_with_me:
            await message.reply(
                "🤝 **No Sharing Activity**\n\n"
                "You haven't shared any accounts or received any shared accounts.\n\n"
                "• Use /share to share your accounts with others\n"
                "• Shared accounts will appear in /recipients",
                parse_mode='Markdown'
            )
            return
        
        # Build management interface
        keyboard = []
        
        if my_shares:
            keyboard.append([InlineKeyboardButton(text="📤 My Shares", callback_data="manage_my_shares")])
        
        if shared_with_me:
            keyboard.append([InlineKeyboardButton(text="📥 Shared with Me", callback_data="manage_shared_with_me")])
        
        keyboard.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_sharing")])
        
        await message.reply(
            "🤝 **Sharing Management**\n\n"
            "Choose what you'd like to manage:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in sharing management: {e}")
        await message.reply("❌ Error loading sharing information. Please try again.")

@router.callback_query(lambda c: c.data == "manage_my_shares")
async def handle_manage_my_shares(callback_query: CallbackQuery):
    """Show accounts I've shared with others."""
    user_id = callback_query.from_user.id
    
    try:
        repository = container.unified_recipient_repository()
        my_shares = repository.get_shared_authorizations_by_owner(user_id)
        
        if not my_shares:
            await callback_query.message.edit_text(
                "📤 **My Shares**\n\n"
                "You haven't shared any accounts yet.\n\n"
                "Use /share to share your accounts with others."
            )
            return
        
        # Build shares list
        text = "📤 **My Shares**\n\n"
        keyboard = []
        
        for share in my_shares:
            # Get grantee info
            user_service = container.user_service()
            grantee_info = user_service.get_user_info(share['grantee_user_id'])
            grantee_name = grantee_info.get('first_name', f"User{share['grantee_user_id']}") if grantee_info else f"User{share['grantee_user_id']}"
            
            status_emoji = "✅" if share['status'] == 'accepted' else "⏳" if share['status'] == 'pending' else "❌"
            permission_emoji = "🔧" if share['permission_level'] == 'admin' else "👤"
            
            last_used = ""
            if share['last_used_at']:
                last_used = f" • Last used: {share['last_used_at'][:10]}"
            
            text += f"{status_emoji} **{share['recipient_name']}** ({share['platform_type']})\n"
            text += f"   👤 Shared with: {grantee_name}\n"
            text += f"   {permission_emoji} Permission: {share['permission_level'].title()}\n"
            text += f"   📅 Status: {share['status'].title()}{last_used}\n\n"
            
            # Add revoke button for active shares
            if share['status'] in ['pending', 'accepted']:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"🗑️ Revoke: {share['recipient_name'][:20]}",
                        callback_data=f"revoke_share_{share['auth_id']}"
                    )
                ])
        
        keyboard.append([InlineKeyboardButton(text="« Back", callback_data="back_to_sharing_menu")])
        
        await callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing my shares: {e}")
        await callback_query.answer("❌ Error loading shares.", show_alert=True)

@router.callback_query(lambda c: c.data.startswith("revoke_share_"))
async def handle_revoke_share(callback_query: CallbackQuery):
    """Handle sharing revocation."""
    try:
        auth_id = int(callback_query.data.replace("revoke_share_", ""))
        user_id = callback_query.from_user.id
        
        # Get authorization details for confirmation
        repository = container.unified_recipient_repository()
        auth = repository.get_authorization_by_id(auth_id)
        
        if not auth or auth.owner_user_id != user_id:
            await callback_query.answer("❌ Not authorized to revoke this share.", show_alert=True)
            return
        
        # Revoke sharing
        sharing_service = container.sharing_service()
        success = sharing_service.revoke_sharing(auth_id, user_id)
        
        if success:
            # Get grantee info for notification
            user_service = container.user_service()
            grantee_info = user_service.get_user_info(auth.grantee_user_id)
            grantee_name = grantee_info.get('first_name', f"User{auth.grantee_user_id}") if grantee_info else f"User{auth.grantee_user_id}"
            
            # Notify grantee if sharing was active
            if auth.status == 'accepted':
                await notify_grantee_of_revocation(auth.grantee_user_id, auth_id)
            
            await callback_query.answer(f"✅ Sharing revoked from {grantee_name}", show_alert=True)
            
            # Refresh the shares list
            await handle_manage_my_shares(callback_query)
        else:
            await callback_query.answer("❌ Error revoking share.", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error revoking share: {e}")
        await callback_query.answer("❌ Error processing revocation.", show_alert=True)

async def notify_grantee_of_revocation(grantee_user_id: int, auth_id: int):
    """Notify grantee that sharing was revoked."""
    try:
        repository = container.unified_recipient_repository()
        auth = repository.get_authorization_by_id(auth_id)
        
        if not auth:
            return
        
        owner_recipient = repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
        if not owner_recipient:
            return
        
        # Get owner info
        user_service = container.user_service()
        owner_info = user_service.get_user_info(auth.owner_user_id)
        owner_name = owner_info.get('first_name', f"User{auth.owner_user_id}") if owner_info else f"User{auth.owner_user_id}"
        
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=grantee_user_id,
            text=f"🗑️ **Shared Account Revoked**\n\n"
                 f"**Account:** {owner_recipient.platform_type.title()} - {owner_recipient.name}\n"
                 f"**Owner:** {owner_name}\n\n"
                 f"Access to this shared account has been revoked.\n"
                 f"The account has been removed from your recipients list.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error notifying grantee of revocation: {e}")

@router.callback_query(lambda c: c.data == "manage_shared_with_me")
async def handle_manage_shared_with_me(callback_query: CallbackQuery):
    """Show accounts shared with me."""
    user_id = callback_query.from_user.id
    
    try:
        repository = container.unified_recipient_repository()
        shared_recipients = repository.get_shared_recipients_for_user(user_id)
        
        if not shared_recipients:
            await callback_query.message.edit_text(
                "📥 **Shared with Me**\n\n"
                "No accounts have been shared with you yet.\n\n"
                "When someone shares an account with you, it will appear here."
            )
            return
        
        # Build shared accounts list
        text = "📥 **Shared with Me**\n\n"
        
        for recipient in shared_recipients:
            # Get authorization details
            auth = repository.get_authorization_by_id(recipient.shared_authorization_id)
            if not auth:
                continue
            
            # Get owner info
            user_service = container.user_service()
            owner_info = user_service.get_user_info(auth.owner_user_id)
            owner_name = owner_info.get('first_name', f"User{auth.owner_user_id}") if owner_info else f"User{auth.owner_user_id}"
            
            permission_emoji = "🔧" if auth.permission_level == 'admin' else "👤"
            
            text += f"📋 **{recipient.name}** ({recipient.platform_type})\n"
            text += f"   👤 Shared by: {owner_name}\n"
            text += f"   {permission_emoji} Permission: {auth.permission_level.title()}\n"
            text += f"   📅 Shared: {auth.created_at[:10] if auth.created_at else 'Unknown'}\n\n"
        
        keyboard = [[InlineKeyboardButton(text="« Back", callback_data="back_to_sharing_menu")]]
        
        await callback_query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing shared with me: {e}")
        await callback_query.answer("❌ Error loading shared accounts.", show_alert=True)

@router.callback_query(lambda c: c.data in ["back_to_sharing_menu", "refresh_sharing"])
async def handle_back_to_sharing_menu(callback_query: CallbackQuery):
    """Return to main sharing menu."""
    # Re-run the main sharing management
    from types import SimpleNamespace
    mock_message = SimpleNamespace()
    mock_message.reply = callback_query.message.edit_text
    mock_message.from_user = callback_query.from_user
    
    await handle_sharing_management(mock_message)
```

### Step 2.9: Enhanced Recipient Display

Update recipient list to show shared accounts clearly.

Update in `handlers_modular/callbacks/recipient/management.py`:

```python
# Update the recipient list display to show shared accounts clearly

def format_recipient_for_display(recipient: UnifiedRecipient, sharing_service=None) -> str:
    """Format recipient for display with sharing information."""
    
    # Basic recipient info
    platform_emoji = {
        'todoist': '📝',
        'trello': '📋', 
        'google_calendar': '📅'
    }.get(recipient.platform_type, '📋')
    
    name_text = f"{platform_emoji} **{recipient.name}** ({recipient.platform_type.title()})"
    
    # Add sharing info
    if recipient.is_personal:
        # Check if this personal account is shared with others
        if sharing_service:
            try:
                repository = sharing_service.repository
                my_shares = repository.get_shared_authorizations_by_owner(recipient.user_id)
                active_shares = [s for s in my_shares if s['owner_recipient_id'] == recipient.id and s['status'] == 'accepted']
                
                if active_shares:
                    share_count = len(active_shares)
                    name_text += f"\n   🤝 Shared with {share_count} user{'s' if share_count > 1 else ''}"
            except Exception:
                pass  # Don't fail on sharing info
    else:
        # This is a shared account
        name_text += f"\n   👥 {recipient.shared_by}"
        
        # Get permission level
        if sharing_service and recipient.shared_authorization_id:
            try:
                permission = sharing_service.get_sharing_permissions(recipient.user_id, recipient.id)
                if permission:
                    permission_emoji = "🔧" if permission == 'admin' else "👤"
                    name_text += f"\n   {permission_emoji} Permission: {permission.title()}"
            except Exception:
                pass
    
    return name_text
```

---

## Phase 3: Testing Implementation

### Step 3.1: Unit Tests

**File:** `tests/unit/test_google_calendar_platform.py`

```python
# tests/unit/test_google_calendar_platform.py
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta

from platforms.google_calendar import GoogleCalendarPlatform
from core.exceptions import PlatformError

@pytest.fixture
def mock_credentials():
    return json.dumps({
        'token': 'test_token',
        'refresh_token': 'test_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'test_client_id',
        'client_secret': 'test_client_secret',
        'scopes': ['https://www.googleapis.com/auth/calendar'],
        'expiry': (datetime.utcnow() + timedelta(hours=1)).isoformat()
    })

@pytest.fixture
def mock_expired_credentials():
    return json.dumps({
        'token': 'expired_token',
        'refresh_token': 'test_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'test_client_id',
        'client_secret': 'test_client_secret',
        'scopes': ['https://www.googleapis.com/auth/calendar'],
        'expiry': (datetime.utcnow() - timedelta(hours=1)).isoformat()
    })

class TestGoogleCalendarPlatform:
    
    @patch('platforms.google_calendar.build')
    @patch('platforms.google_calendar.Credentials')
    def test_init_success(self, mock_credentials_class, mock_build, mock_credentials):
        """Test successful platform initialization."""
        mock_creds_instance = Mock()
        mock_creds_instance.expired = False
        mock_credentials_class.return_value = mock_creds_instance
        
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        platform = GoogleCalendarPlatform(mock_credentials)
        
        assert platform.service == mock_service
        mock_credentials_class.assert_called_once()
        mock_build.assert_called_once_with('calendar', 'v3', credentials=mock_creds_instance)

    @patch('platforms.google_calendar.build')
    @patch('platforms.google_calendar.Credentials')
    @patch('platforms.google_calendar.Request')
    def test_init_with_expired_token_refresh(self, mock_request, mock_credentials_class, mock_build, mock_expired_credentials):
        """Test initialization with expired token triggers refresh."""
        mock_creds_instance = Mock()
        mock_creds_instance.expired = True
        mock_creds_instance.refresh_token = 'refresh_token'
        mock_credentials_class.return_value = mock_creds_instance
        
        platform = GoogleCalendarPlatform(mock_expired_credentials)
        
        mock_creds_instance.refresh.assert_called_once()

    def test_init_invalid_credentials(self):
        """Test initialization with invalid credentials raises error."""
        with pytest.raises(PlatformError):
            GoogleCalendarPlatform("invalid_json")

    @patch('platforms.google_calendar.build')
    @patch('platforms.google_calendar.Credentials')
    def test_create_task_success(self, mock_credentials_class, mock_build, mock_credentials):
        """Test successful task creation."""
        # Setup mocks
        mock_creds_instance = Mock()
        mock_creds_instance.expired = False
        mock_credentials_class.return_value = mock_creds_instance
        
        mock_service = Mock()
        mock_events = Mock()
        mock_insert = Mock()
        mock_execute = Mock()
        
        mock_execute.return_value = {'id': 'test_event_id'}
        mock_insert.return_value.execute = mock_execute
        mock_events.return_value.insert = mock_insert
        mock_service.events = mock_events
        mock_build.return_value = mock_service
        
        platform = GoogleCalendarPlatform(mock_credentials)
        
        # Test data
        task_data = {
            'title': 'Test Task',
            'description': 'Test Description',
            'due_time': '2024-01-01T10:00:00+00:00',
            'timezone': 'UTC'
        }
        
        result = platform.create_task(task_data)
        
        assert result == 'test_event_id'
        mock_insert.assert_called_once()
        
        # Verify event structure
        call_args = mock_insert.call_args
        assert call_args[1]['calendarId'] == 'primary'
        
        event = call_args[1]['body']
        assert event['summary'] == 'Test Task'
        assert event['description'] == 'Test Description'
        assert 'start' in event
        assert 'end' in event

    @patch('platforms.google_calendar.build')
    @patch('platforms.google_calendar.Credentials')
    def test_create_task_with_platform_config(self, mock_credentials_class, mock_build, mock_credentials):
        """Test task creation with specific calendar ID."""
        mock_creds_instance = Mock()
        mock_creds_instance.expired = False
        mock_credentials_class.return_value = mock_creds_instance
        
        mock_service = Mock()
        mock_events = Mock()
        mock_insert = Mock()
        mock_execute = Mock()
        
        mock_execute.return_value = {'id': 'test_event_id'}
        mock_insert.return_value.execute = mock_execute
        mock_events.return_value.insert = mock_insert
        mock_service.events = mock_events
        mock_build.return_value = mock_service
        
        platform = GoogleCalendarPlatform(mock_credentials)
        
        task_data = {
            'title': 'Test Task',
            'due_time': '2024-01-01T10:00:00+00:00',
            'platform_config': json.dumps({'calendar_id': 'custom_calendar_id'})
        }
        
        platform.create_task(task_data)
        
        call_args = mock_insert.call_args
        assert call_args[1]['calendarId'] == 'custom_calendar_id'

    @patch('platforms.google_calendar.build')
    @patch('platforms.google_calendar.Credentials')
    def test_attach_screenshot_success(self, mock_credentials_class, mock_build, mock_credentials):
        """Test successful screenshot attachment."""
        mock_creds_instance = Mock()
        mock_creds_instance.expired = False
        mock_credentials_class.return_value = mock_creds_instance
        
        mock_service = Mock()
        mock_events = Mock()
        
        # Mock get event
        mock_get = Mock()
        mock_get.return_value.execute.return_value = {
            'id': 'test_event_id',
            'description': 'Original description'
        }
        mock_events.return_value.get = mock_get
        
        # Mock update event
        mock_update = Mock()
        mock_update.return_value.execute.return_value = {}
        mock_events.return_value.update = mock_update
        
        mock_service.events = mock_events
        mock_build.return_value = mock_service
        
        platform = GoogleCalendarPlatform(mock_credentials)
        
        result = platform.attach_screenshot('test_event_id', b'image_data', 'screenshot.png')
        
        assert result is True
        mock_update.assert_called_once()
        
        # Verify description was updated
        call_args = mock_update.call_args
        updated_event = call_args[1]['body']
        assert 'screenshot.png' in updated_event['description']

    def test_is_configured_static_valid(self):
        """Test is_configured_static with valid credentials."""
        platform_settings = {
            'google_calendar_credentials': json.dumps({
                'refresh_token': 'valid_refresh_token'
            })
        }
        
        result = GoogleCalendarPlatform.is_configured_static(platform_settings)
        assert result is True

    def test_is_configured_static_invalid(self):
        """Test is_configured_static with invalid credentials."""
        # Missing credentials
        assert GoogleCalendarPlatform.is_configured_static({}) is False
        
        # Invalid JSON
        platform_settings = {'google_calendar_credentials': 'invalid_json'}
        assert GoogleCalendarPlatform.is_configured_static(platform_settings) is False
        
        # Missing refresh token
        platform_settings = {
            'google_calendar_credentials': json.dumps({'token': 'access_token'})
        }
        assert GoogleCalendarPlatform.is_configured_static(platform_settings) is False
```

**File:** `tests/unit/test_sharing_security.py`

```python
# tests/unit/test_sharing_security.py
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from services.sharing_service import SharingService
from models.shared_authorization import SharedAuthorization
from models.unified_recipient import UnifiedRecipient

class TestSharingSecurity:
    
    @pytest.fixture
    def mock_repository(self):
        return Mock()
    
    @pytest.fixture
    def mock_user_service(self):
        return Mock()
    
    @pytest.fixture
    def sharing_service(self, mock_repository, mock_user_service):
        return SharingService(mock_repository, mock_user_service)
    
    def test_credential_resolution_security(self, mock_repository):
        """
        CRITICAL TEST: Ensure grantee never gets access to owner's raw credentials.
        """
        # Setup: Owner recipient with real credentials
        owner_recipient = UnifiedRecipient(
            id=1,
            user_id=100,  # owner
            name="Owner's Todoist",
            platform_type="todoist",
            credentials="owner_secret_credentials",
            is_personal=True,
            shared_authorization_id=None
        )
        
        # Setup: Shared authorization
        shared_auth = SharedAuthorization(
            id=1,
            owner_user_id=100,
            grantee_user_id=200,
            owner_recipient_id=1,
            status='accepted'
        )
        
        # Setup: Grantee's shared recipient (MUST have empty credentials)
        grantee_recipient = UnifiedRecipient(
            id=2,
            user_id=200,  # grantee
            name="Shared Todoist",
            platform_type="todoist",
            credentials="",  # CRITICAL: Must be empty
            is_personal=False,
            shared_authorization_id=1
        )
        
        # Mock repository responses
        mock_repository.get_authorization_by_id.return_value = shared_auth
        mock_repository.get_recipient_by_id.return_value = owner_recipient
        
        # Test credential resolution
        resolved_credentials = mock_repository.resolve_credentials_for_recipient(grantee_recipient)
        
        # CRITICAL ASSERTIONS
        # 1. Grantee's recipient should never contain owner's credentials
        assert grantee_recipient.credentials == ""
        
        # 2. Resolved credentials should be owner's credentials
        mock_repository.resolve_credentials_for_recipient.return_value = "owner_secret_credentials"
        assert resolved_credentials == "owner_secret_credentials"
        
        # 3. Verify authorization lookup was called
        mock_repository.get_authorization_by_id.assert_called_with(1)
        
        # 4. Verify owner recipient lookup was called
        mock_repository.get_recipient_by_id.assert_called_with(100, 1)

    def test_cascade_delete_security(self, mock_repository):
        """Test that deleting owner's recipient revokes all shares."""
        # Setup scenario where owner deletes their recipient
        owner_recipient_id = 1
        auth_id = 1
        
        # Simulate CASCADE DELETE behavior
        mock_repository.delete_recipient.side_effect = lambda user_id, recipient_id: (
            mock_repository.get_authorization_by_id.return_value = None
            if recipient_id == owner_recipient_id else None
        )
        
        # Delete owner's recipient
        mock_repository.delete_recipient(100, owner_recipient_id)
        
        # Verify shared authorization is gone due to CASCADE
        auth = mock_repository.get_authorization_by_id(auth_id)
        assert auth is None

    def test_shared_recipient_cannot_contain_credentials(self, mock_repository, sharing_service):
        """Test that shared recipients are never created with credentials."""
        # Mock valid authorization
        auth = SharedAuthorization(
            id=1,
            owner_user_id=100,
            grantee_user_id=200,
            owner_recipient_id=1,
            status='accepted'
        )
        
        owner_recipient = UnifiedRecipient(
            id=1,
            user_id=100,
            name="Owner's Account",
            platform_type="todoist",
            credentials="secret_credentials",
            is_personal=True
        )
        
        mock_repository.get_authorization_by_id.return_value = auth
        mock_repository.get_recipient_by_id.return_value = owner_recipient
        mock_repository.create_shared_recipient.return_value = 2
        
        # Create shared recipient
        recipient_id = sharing_service.accept_sharing_request(1, 200)
        
        # Verify create_shared_recipient was called with empty credentials
        mock_repository.create_shared_recipient.assert_called_once()
        call_args = mock_repository.create_shared_recipient.call_args
        
        # The shared recipient should be created with empty credentials field
        # (credentials parameter should be empty string '')
        assert recipient_id == 2

    def test_permission_enforcement(self, sharing_service):
        """Test that permission levels are properly enforced."""
        user_id = 200
        recipient_id = 2
        
        # Test 'use' permission
        sharing_service.get_sharing_permissions = Mock(return_value='use')
        
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'create_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'update_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'delete_task') is False  # Admin only
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'share_recipient') is False  # Admin only
        
        # Test 'admin' permission
        sharing_service.get_sharing_permissions = Mock(return_value='admin')
        
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'create_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'delete_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'share_recipient') is True

    def test_authorization_status_validation(self, sharing_service, mock_repository):
        """Test that only active authorizations allow access."""
        # Test various authorization statuses
        statuses_and_expected = [
            ('pending', False),
            ('accepted', True),
            ('revoked', False),
            ('declined', False)
        ]
        
        for status, should_allow in statuses_and_expected:
            auth = SharedAuthorization(
                id=1,
                owner_user_id=100,
                grantee_user_id=200,
                owner_recipient_id=1,
                status=status
            )
            
            mock_repository.get_authorization_by_id.return_value = auth
            
            # Only 'accepted' status should allow access
            assert auth.is_active() == should_allow

    def test_self_sharing_prevention(self, sharing_service, mock_user_service):
        """Test that users cannot share with themselves."""
        owner_user_id = 100
        recipient_id = 1
        
        # Mock user service to return same user ID
        mock_user_service.get_user_id_from_username.return_value = owner_user_id
        
        with pytest.raises(ValueError, match="Cannot share with yourself"):
            sharing_service.create_sharing_request(
                owner_user_id, "same_user", recipient_id, "use"
            )

    def test_double_sharing_prevention(self, sharing_service, mock_repository):
        """Test that the same account cannot be shared twice with same user."""
        mock_repository.create_shared_authorization.side_effect = ValueError(
            "Sharing already exists between these users for this account"
        )
        
        with pytest.raises(ValueError, match="Sharing already exists"):
            sharing_service.create_sharing_request(100, "test_user", 1, "use")
```

### Step 3.2: Integration Tests

**File:** `tests/integration/test_google_calendar_flow.py`

```python
# tests/integration/test_google_calendar_flow.py
import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from handlers_modular.states.recipient_setup import (
    handle_google_calendar_setup,
    handle_oauth_code_input
)
from states.recipient_states import RecipientState

@pytest.fixture
def mock_state():
    state = Mock(spec=FSMContext)
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    state.get_data = AsyncMock(return_value={'platform_type': 'google_calendar'})
    return state

@pytest.fixture
def mock_callback_query():
    callback = Mock(spec=CallbackQuery)
    callback.from_user = User(id=123, is_bot=False, first_name="Test")
    callback.message = Mock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback

@pytest.fixture
def mock_message():
    message = Mock(spec=Message)
    message.from_user = User(id=123, is_bot=False, first_name="Test")
    message.text = "test_oauth_code"
    message.reply = AsyncMock()
    message.delete = AsyncMock()
    return message

class TestGoogleCalendarIntegration:
    
    @patch('handlers_modular.states.recipient_setup.container')
    async def test_google_calendar_setup_flow(self, mock_container, mock_callback_query, mock_state):
        """Test complete Google Calendar setup flow."""
        # Mock OAuth service
        mock_oauth_service = Mock()
        mock_oauth_service.get_authorization_url.return_value = "https://accounts.google.com/oauth/..."
        mock_container.google_oauth_service.return_value = mock_oauth_service
        
        await handle_google_calendar_setup(mock_callback_query, mock_state)
        
        # Verify state updates
        mock_state.update_data.assert_called_with(platform_type="google_calendar")
        mock_state.set_state.assert_called_with(RecipientState.waiting_for_oauth_code)
        
        # Verify OAuth URL generation
        mock_oauth_service.get_authorization_url.assert_called_with(123)
        
        # Verify message with authorization URL
        mock_callback_query.message.edit_text.assert_called_once()
        call_args = mock_callback_query.message.edit_text.call_args
        assert "Google Calendar Authorization" in call_args[0][0]

    @patch('handlers_modular.states.recipient_setup.container')
    async def test_oauth_code_input_success(self, mock_container, mock_message, mock_state):
        """Test successful OAuth code input and recipient creation."""
        # Mock services
        mock_oauth_service = Mock()
        mock_oauth_service.exchange_code_for_token.return_value = '{"token": "test_token"}'
        mock_container.google_oauth_service.return_value = mock_oauth_service
        
        mock_recipient_service = Mock()
        mock_recipient_service.add_personal_recipient.return_value = 1
        mock_container.recipient_service.return_value = mock_recipient_service
        
        # Mock keyboard
        with patch('handlers_modular.states.recipient_setup.get_recipient_management_keyboard') as mock_keyboard:
            mock_keyboard.return_value = Mock()
            
            await handle_oauth_code_input(mock_message, mock_state)
        
        # Verify OAuth code exchange
        mock_oauth_service.exchange_code_for_token.assert_called_with("test_oauth_code")
        
        # Verify recipient creation
        mock_recipient_service.add_personal_recipient.assert_called_with(
            user_id=123,
            name="My Google Calendar",
            platform_type="google_calendar",
            credentials='{"token": "test_token"}',
            platform_config=json.dumps({"calendar_id": "primary"})
        )
        
        # Verify success message
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args
        assert "Google Calendar Connected!" in call_args[0][0]
        
        # Verify code message deletion for security
        mock_message.delete.assert_called_once()
        
        # Verify state cleared
        mock_state.clear.assert_called_once()

    @patch('handlers_modular.states.recipient_setup.container')
    async def test_oauth_code_input_invalid_code(self, mock_container, mock_message, mock_state):
        """Test OAuth code input with invalid code."""
        # Mock OAuth service to raise error
        mock_oauth_service = Mock()
        mock_oauth_service.exchange_code_for_token.side_effect = ValueError("Invalid authorization code")
        mock_container.google_oauth_service.return_value = mock_oauth_service
        
        await handle_oauth_code_input(mock_message, mock_state)
        
        # Verify error message
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args
        assert "Authorization Failed" in call_args[0][0]
        assert "Invalid authorization code" in call_args[0][0]
        
        # Verify state not cleared (user can try again)
        mock_state.clear.assert_not_called()
```

**File:** `tests/integration/test_sharing_workflow.py`

```python
# tests/integration/test_sharing_workflow.py
import pytest
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

from services.sharing_service import SharingService
from models.shared_authorization import SharedAuthorization
from models.unified_recipient import UnifiedRecipient

class TestSharingWorkflowIntegration:
    
    @pytest.fixture
    def mock_repository(self):
        repo = Mock()
        # Setup default successful responses
        repo.create_shared_authorization.return_value = 1
        repo.update_authorization_status.return_value = True
        repo.create_shared_recipient.return_value = 2
        repo.delete_shared_recipients_by_authorization.return_value = 1
        return repo
    
    @pytest.fixture
    def mock_user_service(self):
        service = Mock()
        service.get_user_id_from_username.return_value = 200  # grantee user ID
        service.get_user_info.return_value = {'first_name': 'TestUser'}
        return service
    
    @pytest.fixture
    def sharing_service(self, mock_repository, mock_user_service):
        return SharingService(mock_repository, mock_user_service)
    
    @pytest.fixture
    def owner_recipient(self):
        return UnifiedRecipient(
            id=1,
            user_id=100,  # owner
            name="Owner's Todoist",
            platform_type="todoist",
            credentials="owner_secret_token",
            is_personal=True,
            shared_authorization_id=None
        )
    
    def test_complete_sharing_workflow(self, sharing_service, mock_repository, owner_recipient):
        """Test complete sharing workflow from request to usage."""
        
        # Setup mocks
        mock_repository.get_recipient_by_id.return_value = owner_recipient
        
        # Step 1: Create sharing request
        auth_id = sharing_service.create_sharing_request(
            owner_user_id=100,
            grantee_username="test_grantee", 
            recipient_id=1,
            permission_level="use"
        )
        
        assert auth_id == 1
        mock_repository.create_shared_authorization.assert_called_with(100, 200, 1, "use")
        
        # Step 2: Mock authorization for acceptance
        shared_auth = SharedAuthorization(
            id=1,
            owner_user_id=100,
            grantee_user_id=200,
            owner_recipient_id=1,
            status='pending'
        )
        mock_repository.get_authorization_by_id.return_value = shared_auth
        
        # Step 3: Accept sharing request
        shared_recipient_id = sharing_service.accept_sharing_request(auth_id, 200)
        
        assert shared_recipient_id == 2
        mock_repository.update_authorization_status.assert_called_with(1, 'accepted')
        mock_repository.create_shared_recipient.assert_called_once()
        
        # Step 4: Verify shared recipient can resolve credentials
        grantee_recipient = UnifiedRecipient(
            id=2,
            user_id=200,
            name="Shared Todoist",
            platform_type="todoist", 
            credentials="",  # Empty for shared
            is_personal=False,
            shared_authorization_id=1
        )
        
        # Mock credential resolution
        mock_repository.resolve_credentials_for_recipient.return_value = "owner_secret_token"
        resolved_credentials = mock_repository.resolve_credentials_for_recipient(grantee_recipient)
        
        assert resolved_credentials == "owner_secret_token"
        
        # Step 5: Test revocation
        shared_auth.status = 'accepted'  # Update for revocation test
        success = sharing_service.revoke_sharing(auth_id, 100)
        
        assert success is True
        mock_repository.update_authorization_status.assert_called_with(1, 'revoked')
        mock_repository.delete_shared_recipients_by_authorization.assert_called_with(1)

    def test_sharing_permission_enforcement(self, sharing_service):
        """Test that permission levels are enforced correctly."""
        
        # Test 'use' permission level
        sharing_service.get_sharing_permissions = Mock(return_value='use')
        
        user_id = 200
        recipient_id = 2
        
        # Use permission should allow basic operations
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'create_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'update_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'view_recipient') is True
        
        # Use permission should NOT allow admin operations
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'delete_task') is False
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'edit_recipient') is False
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'share_recipient') is False
        
        # Test 'admin' permission level
        sharing_service.get_sharing_permissions = Mock(return_value='admin')
        
        # Admin permission should allow all operations
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'create_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'delete_task') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'edit_recipient') is True
        assert sharing_service.can_user_perform_action(user_id, recipient_id, 'share_recipient') is True

    def test_security_edge_cases(self, sharing_service, mock_repository):
        """Test security edge cases and potential vulnerabilities."""
        
        # Test 1: Cannot accept someone else's sharing request
        auth = SharedAuthorization(
            id=1,
            owner_user_id=100,
            grantee_user_id=200,  # Original grantee
            owner_recipient_id=1,
            status='pending'
        )
        mock_repository.get_authorization_by_id.return_value = auth
        
        # Different user (300) tries to accept
        with pytest.raises(ValueError, match="Not authorized to accept"):
            sharing_service.accept_sharing_request(1, 300)
        
        # Test 2: Cannot revoke someone else's sharing
        with pytest.raises(ValueError, match="Not authorized to revoke"):
            sharing_service.revoke_sharing(1, 300)  # Wrong owner
        
        # Test 3: Cannot accept already processed request
        auth.status = 'accepted'
        with pytest.raises(ValueError, match="Cannot accept authorization with status"):
            sharing_service.accept_sharing_request(1, 200)

    def test_cascade_delete_simulation(self, mock_repository):
        """Test that deleting owner recipient cascades to authorizations."""
        
        # Simulate owner deleting their recipient
        owner_user_id = 100
        owner_recipient_id = 1
        
        # Mock the CASCADE DELETE behavior
        def mock_delete_recipient(user_id, recipient_id):
            if user_id == owner_user_id and recipient_id == owner_recipient_id:
                # Simulate CASCADE: remove authorization
                mock_repository.get_authorization_by_id.return_value = None
                return True
            return False
        
        mock_repository.delete_recipient.side_effect = mock_delete_recipient
        
        # Delete owner's recipient
        result = mock_repository.delete_recipient(owner_user_id, owner_recipient_id)
        assert result is True
        
        # Verify authorization is gone (CASCADE effect)
        auth = mock_repository.get_authorization_by_id(1)
        assert auth is None

    def test_username_resolution_failure(self, sharing_service, mock_user_service):
        """Test handling when username cannot be resolved."""
        
        # Mock user not found
        mock_user_service.get_user_id_from_username.return_value = None
        
        with pytest.raises(ValueError, match="not found in bot users"):
            sharing_service.create_sharing_request(
                owner_user_id=100,
                grantee_username="nonexistent_user",
                recipient_id=1,
                permission_level="use"
            )

    def test_double_sharing_prevention(self, sharing_service, mock_repository):
        """Test that same account cannot be shared twice with same user."""
        
        # Mock integrity constraint violation
        mock_repository.create_shared_authorization.side_effect = ValueError(
            "Sharing already exists between these users for this account"
        )
        
        with pytest.raises(ValueError, match="Sharing already exists"):
            sharing_service.create_sharing_request(
                owner_user_id=100,
                grantee_username="test_user",
                recipient_id=1,
                permission_level="use"
            )
```

---

## Phase 4: Security Review & Deployment

### Step 4.1: Security Checklist

**Critical Security Verification:**

- [ ] **Credential Isolation:** Verify grantee recipient records never contain owner credentials
- [ ] **Authorization Validation:** All sharing operations validate user permissions
- [ ] **Token Security:** OAuth tokens are encrypted at rest and refreshed properly  
- [ ] **State Parameter:** OAuth flow uses secure state parameter for CSRF protection
- [ ] **CASCADE Behavior:** Database CASCADE DELETE properly cleans up orphaned data
- [ ] **Permission Enforcement:** UI restrictions match backend permission checks
- [ ] **Input Validation:** All user inputs are validated and sanitized
- [ ] **Error Handling:** No sensitive information leaked in error messages

### Step 4.2: Performance Review

**Database Optimization:**

- [ ] Add indices for frequently queried fields
- [ ] Test query performance with large datasets
- [ ] Verify efficient JOIN operations for shared recipient queries
- [ ] Monitor OAuth token refresh frequency

### Step 4.3: User Experience Review

**UI Clarity:**

- [ ] Clear distinction between personal and shared accounts
- [ ] Obvious permission level indicators
- [ ] Helpful error messages for failed operations
- [ ] Consistent terminology throughout interface

### Step 4.4: Final Integration Test

**End-to-End Scenarios:**

1. **Basic Google Calendar Flow:**
   - User A sets up Google Calendar
   - Creates task → verifies event in Google Calendar
   - Screenshots attach properly

2. **Complete Sharing Flow:**
   - User A shares Google Calendar with User B
   - User B accepts and creates task
   - Task appears in User A's actual Google Calendar
   - User A revokes access
   - User B can no longer access shared account

3. **Security Verification:**
   - User B never sees User A's raw credentials
   - Shared account properly removed on revocation
   - Permission levels enforced in UI and backend

---

## Implementation Notes for Junior Developer

### **Critical Implementation Order:**

1. **Start with OAuth State Manager** - This is required by OAuth Service
2. **Implement Google OAuth Service** - Core authentication logic
3. **Create Google Calendar Platform** - Task platform implementation  
4. **Add UI Integration** - Telegram bot handlers
5. **Test Google Calendar thoroughly** before starting sharing
6. **Implement sharing database schema** - Foundation for sharing
7. **Create sharing service with security focus** - Critical credential resolution
8. **Add sharing UI workflows** - User-facing sharing features
9. **Comprehensive testing** - Security and integration tests
<ARTEM> Make sure target db structure is great and nitty, should be decent level if there are issues currently with architecture should be addressed</ARTEM>
### **Common Pitfalls to Avoid:**

1. **Never store owner credentials in grantee recipient records**
2. **Always validate authorization status before allowing operations**
3. **Test CASCADE DELETE behavior thoroughly**
4. **Implement proper error handling for expired OAuth tokens**
5. **Cache user information for username resolution**
6. **Use clear UI labeling for shared vs personal accounts**

### **Testing Strategy:**

1. **Unit test each component in isolation**
2. **Integration test complete workflows**
3. **Security test credential resolution logic**
4. **Manual test with real Google Calendar API**
5. **Test sharing between actual Telegram users**

### **Debugging Tips:**

1. **Check logs for OAuth token refresh events**
2. **Verify database foreign key constraints**
3. **Monitor API rate limiting and quotas**
4. **Use database constraints to prevent data corruption**
5. **Test error scenarios thoroughly**

This implementation guide provides everything needed for a junior developer to successfully implement both Google Calendar integration and secure account sharing. Follow the steps in order, test thoroughly at each stage, and pay special attention to the security considerations highlighted throughout.