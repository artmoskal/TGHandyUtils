# Architecture Blueprint: Google Calendar & Secure Account Sharing

A comprehensive implementation guide for adding Google Calendar integration and secure account sharing to the Telegram bot. This blueprint focuses on clean architecture, security-first design, and production-ready implementation patterns.

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

## Phase 0: Integration with Existing Docker Infrastructure

### Step 0.1: Update Environment Configuration

**Update:** `docker-compose.yml` (add Google Calendar environment variables)

```yaml
version: '3.8'

services:
  bot:
    build:
      context: .
      dockerfile: infra/Dockerfile
    volumes:
      - .:/app
      - ./data:/app/data
    environment:
      - PYTHONPATH=/app
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - TODOIST_API_TOKEN=${TODOIST_API_TOKEN:-}
      - TRELLO_API_KEY=${TRELLO_API_KEY:-}
      - TRELLO_TOKEN=${TRELLO_TOKEN:-}
      # Add Google Calendar OAuth variables
      - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID:-}
      - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET:-}
    working_dir: /app
    command: bash -c "source /opt/conda/etc/profile.d/conda.sh && conda activate TGHandyUtils && python main.py"
    restart: unless-stopped
```

### Step 0.2: Update Environment File

**Update:** `.env` (add these variables to your existing `.env` file)

```bash
# Existing variables...
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here

# Add Google Calendar OAuth Configuration
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
```

### Step 0.3: Update Dependencies

**Update:** `environment.yml` (add Google Calendar dependencies)

```yaml
name: TGHandyUtils
channels:
  - conda-forge
  - defaults
dependencies:
  - python=3.10
  - pip
  # Existing dependencies...
  - pip:
    # Existing pip packages...
    - google-api-python-client==2.88.0
    - google-auth-httplib2==0.1.0
    - google-auth-oauthlib==1.0.0
```

### Step 0.4: Deploy with Existing Infrastructure

Use the existing Docker setup:

```bash
# Build and run with the existing Docker infrastructure
docker-compose up -d --build

# Or use the infra setup
docker-compose -f infra/docker-compose.yml up -d --build
```

---

## Phase 1: Clean Database Architecture

### Step 1.1: Database Schema - From Scratch

**File:** `database/schema.sql`

```sql
-- Enable foreign key support
PRAGMA foreign_keys = ON;

-- Users table (cache user information)
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT UNIQUE,
    first_name TEXT,
    last_name TEXT,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Recipients table (both personal and shared)
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    platform_type TEXT NOT NULL,
    credentials TEXT NOT NULL DEFAULT '', -- Empty for shared recipients
    platform_config TEXT, -- JSON configuration
    is_personal BOOLEAN NOT NULL DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE,
    enabled BOOLEAN DEFAULT TRUE,
    shared_authorization_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (shared_authorization_id) REFERENCES shared_authorizations(id) ON DELETE CASCADE
);

-- Shared authorizations table
CREATE TABLE IF NOT EXISTS shared_authorizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    grantee_user_id INTEGER NOT NULL,
    owner_recipient_id INTEGER NOT NULL,
    permission_level TEXT NOT NULL DEFAULT 'use' CHECK (permission_level IN ('use', 'admin')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked', 'declined')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id),
    FOREIGN KEY (grantee_user_id) REFERENCES users(user_id),
    FOREIGN KEY (owner_recipient_id) REFERENCES recipients(id) ON DELETE CASCADE,
    UNIQUE(owner_user_id, grantee_user_id, owner_recipient_id)
);

-- Authentication requests table (new workflow)
CREATE TABLE IF NOT EXISTS auth_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    requester_user_id INTEGER NOT NULL,
    target_user_id INTEGER NOT NULL,
    platform_type TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'expired', 'cancelled')),
    expires_at TIMESTAMP NOT NULL,
    completed_recipient_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (requester_user_id) REFERENCES users(user_id),
    FOREIGN KEY (target_user_id) REFERENCES users(user_id),
    FOREIGN KEY (completed_recipient_id) REFERENCES recipients(id) ON DELETE SET NULL
);

-- OAuth states table
CREATE TABLE IF NOT EXISTS oauth_states (
    user_id INTEGER NOT NULL,
    state TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    oauth_code TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, state),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    platform_task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    due_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (recipient_id) REFERENCES recipients(id) ON DELETE CASCADE
);

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_recipients_user_id ON recipients(user_id);
CREATE INDEX IF NOT EXISTS idx_shared_auth_grantee ON shared_authorizations(grantee_user_id, status);
CREATE INDEX IF NOT EXISTS idx_shared_auth_owner ON shared_authorizations(owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_auth_requests_target ON auth_requests(target_user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_user_recipient ON tasks(user_id, recipient_id);

-- Triggers for updated_at
CREATE TRIGGER IF NOT EXISTS update_recipients_timestamp 
AFTER UPDATE ON recipients
BEGIN
    UPDATE recipients SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_shared_auth_timestamp 
AFTER UPDATE ON shared_authorizations
BEGIN
    UPDATE shared_authorizations SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS update_auth_requests_timestamp 
AFTER UPDATE ON auth_requests
BEGIN
    UPDATE auth_requests SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
```

### Step 1.2: Migration System

**File:** `database/migrations/migrate.py`

```python
# database/migrations/migrate.py
import sqlite3
import os
from pathlib import Path
from core.logger import logger

class MigrationManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection = None

    def migrate(self):
        """Run all migrations from scratch."""
        try:
            # Ensure database directory exists
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            self.connection = sqlite3.connect(self.db_path)
            self.connection.execute("PRAGMA foreign_keys = ON")
            
            # Read and execute schema
            schema_path = Path(__file__).parent.parent / "schema.sql"
            with open(schema_path, 'r') as f:
                schema_sql = f.read()
            
            self.connection.executescript(schema_sql)
            self.connection.commit()
            
            logger.info("Database schema created successfully")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            if self.connection:
                self.connection.rollback()
            raise
        finally:
            if self.connection:
                self.connection.close()

if __name__ == "__main__":
    import sys
    db_path = os.getenv("DATABASE_PATH", "data/tghandyutils.db")
    manager = MigrationManager(db_path)
    manager.migrate()
```

---

## Phase 2: Google Calendar Integration

### Step 2.1: OAuth State Management

**File:** `services/oauth_state_manager.py`

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

    def create_pending_request(self, user_id: int) -> str:
        """Create unique state for OAuth request with 1 hour expiration."""
        state = f"{user_id}_{uuid.uuid4().hex[:8]}"
        expires_at = datetime.utcnow() + timedelta(hours=1)
        
        # Clean up expired states
        self.connection.execute(
            "DELETE FROM oauth_states WHERE expires_at < ?",
            (datetime.utcnow(),)
        )
        
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
            
            # Verify state exists and not expired
            cursor = self.connection.execute(
                "SELECT user_id FROM oauth_states WHERE user_id = ? AND state = ? AND expires_at > ?",
                (user_id, state, datetime.utcnow())
            )
            
            if not cursor.fetchone():
                logger.warning(f"Invalid or expired OAuth state: {state}")
                return None
            
            # Store OAuth code
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
            "SELECT oauth_code, state FROM oauth_states WHERE user_id = ? AND oauth_code IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        )
        
        row = cursor.fetchone()
        if row:
            code, state = row
            # Delete after retrieval
            self.connection.execute(
                "DELETE FROM oauth_states WHERE user_id = ? AND state = ?",
                (user_id, state)
            )
            self.connection.commit()
            return code
        
        return None
```

### Step 2.2: Google OAuth Service

**File:** `services/google_oauth_service.py`

```python
# services/google_oauth_service.py
import json
from typing import Optional
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from core.logger import logger
from core.exceptions import OAuthError

class GoogleOAuthService:
    def __init__(self, client_id: str, client_secret: str):
        self.client_secrets_config = {
            "installed": {
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
            raise OAuthError(f"Invalid authorization code: {str(e)}")

    def refresh_token(self, credentials_json: str) -> str:
        """Refresh expired access token."""
        try:
            creds_data = json.loads(credentials_json)
            credentials = Credentials(**creds_data)
            
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                
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
            
            return credentials_json
            
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            raise OAuthError(f"Failed to refresh token: {str(e)}")
```

### Step 2.3: Google Calendar Platform

**File:** `platforms/google_calendar.py`

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
from core.exceptions import PlatformError, OAuthError
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
                self._credentials_refreshed = True
            else:
                self._credentials_refreshed = False
            
            self.service = build('calendar', 'v3', credentials=self.credentials)
            logger.info("Google Calendar platform initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize Google Calendar platform: {e}")
            raise PlatformError(f"Invalid Google Calendar credentials: {str(e)}")

    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """Create calendar event from task data."""
        try:
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
            
            end_dt = start_dt + timedelta(hours=1)
            
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
            
            if task_data.get('location'):
                event['location'] = task_data['location']
            
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
                raise OAuthError("Google Calendar authentication expired. Please reconnect your account.")
            elif e.resp.status == 403:
                logger.error("Google Calendar access forbidden")
                raise PlatformError("Access to Google Calendar denied. Please check permissions.")
            else:
                logger.error(f"Google Calendar API error: {e}")
                raise PlatformError(f"Failed to create calendar event: {str(e)}")
        except Exception as e:
            logger.error(f"Error creating Google Calendar event: {e}")
            raise PlatformError(f"Failed to create calendar event: {str(e)}")

    def get_updated_credentials(self) -> Optional[str]:
        """Get updated credentials if they were refreshed."""
        if self._credentials_refreshed:
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
        return None

    # ... implement other required methods ...
```

---

## Phase 3: Enhanced Sharing Workflow with Authentication Requests

### Step 3.1: Authentication Request Model

**File:** `models/auth_request.py`

```python
# models/auth_request.py
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class AuthRequest:
    id: int
    requester_user_id: int
    target_user_id: int
    platform_type: str
    recipient_name: str
    status: str = 'pending'  # 'pending', 'completed', 'expired', 'cancelled'
    expires_at: datetime = None
    completed_recipient_id: Optional[int] = None
    created_at: datetime = None
    updated_at: datetime = None
    
    def is_active(self) -> bool:
        return self.status == 'pending' and self.expires_at > datetime.utcnow()
    
    def is_completed(self) -> bool:
        return self.status == 'completed'
```

### Step 3.2: Enhanced Sharing Service

**File:** `services/sharing_service.py`

```python
# services/sharing_service.py
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from core.logger import logger
from models.shared_authorization import SharedAuthorization
from models.auth_request import AuthRequest
from core.exceptions import SharingError

class SharingService:
    def __init__(self, repository, user_service):
        self.repository = repository
        self.user_service = user_service

    # Existing sharing methods...

    def create_auth_request(self, requester_user_id: int, target_username: str, 
                          platform_type: str, recipient_name: str) -> int:
        """Create authentication request for new shared account."""
        try:
            # Get target user_id
            target_user_id = self.user_service.get_user_id_from_username(target_username)
            if not target_user_id:
                raise ValueError(f"User @{target_username} not found in bot users")
            
            if target_user_id == requester_user_id:
                raise ValueError("Cannot request authentication from yourself")
            
            # Validate platform type
            if platform_type not in ['todoist', 'trello', 'google_calendar']:
                raise ValueError(f"Invalid platform type: {platform_type}")
            
            # Create auth request with 24 hour expiration
            expires_at = datetime.utcnow() + timedelta(hours=24)
            
            auth_request_id = self.repository.create_auth_request(
                requester_user_id=requester_user_id,
                target_user_id=target_user_id,
                platform_type=platform_type,
                recipient_name=recipient_name,
                expires_at=expires_at
            )
            
            logger.info(f"Created auth request {auth_request_id}: {requester_user_id} -> {target_username}")
            return auth_request_id
            
        except Exception as e:
            logger.error(f"Error creating auth request: {e}")
            raise

    def get_pending_auth_requests(self, user_id: int) -> List[AuthRequest]:
        """Get pending authentication requests for a user."""
        return self.repository.get_pending_auth_requests_for_user(user_id)

    def complete_auth_request(self, auth_request_id: int, target_user_id: int, 
                            credentials: str, platform_config: str = None) -> int:
        """Complete authentication request by creating recipient."""
        try:
            # Validate auth request
            auth_request = self.repository.get_auth_request_by_id(auth_request_id)
            if not auth_request:
                raise ValueError("Authentication request not found")
            
            if auth_request.target_user_id != target_user_id:
                raise ValueError("Not authorized to complete this request")
            
            if not auth_request.is_active():
                raise ValueError("Authentication request expired or not active")
            
            # Create recipient for requester
            recipient_id = self.repository.add_personal_recipient(
                user_id=auth_request.requester_user_id,
                name=auth_request.recipient_name,
                platform_type=auth_request.platform_type,
                credentials=credentials,
                platform_config=platform_config
            )
            
            # Update auth request status
            self.repository.update_auth_request_status(
                auth_request_id, 'completed', recipient_id
            )
            
            logger.info(f"Completed auth request {auth_request_id}, created recipient {recipient_id}")
            return recipient_id
            
        except Exception as e:
            logger.error(f"Error completing auth request: {e}")
            raise

    def cancel_auth_request(self, auth_request_id: int, user_id: int) -> bool:
        """Cancel authentication request."""
        try:
            auth_request = self.repository.get_auth_request_by_id(auth_request_id)
            if not auth_request:
                return False
            
            # Only requester or target can cancel
            if user_id not in [auth_request.requester_user_id, auth_request.target_user_id]:
                raise ValueError("Not authorized to cancel this request")
            
            return self.repository.update_auth_request_status(auth_request_id, 'cancelled')
            
        except Exception as e:
            logger.error(f"Error cancelling auth request: {e}")
            return False

    def cleanup_expired_requests(self):
        """Clean up expired authentication requests."""
        try:
            count = self.repository.cleanup_expired_auth_requests()
            if count > 0:
                logger.info(f"Cleaned up {count} expired authentication requests")
            return count
        except Exception as e:
            logger.error(f"Error cleaning up expired requests: {e}")
            return 0
```

### Step 3.3: UI Workflows with Corner Cases

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
from states.sharing_states import SharingState, AuthRequestState

router = Router()

@router.message(Command("share"))
async def handle_share_command(message: Message, state: FSMContext):
    """Start sharing workflow with two options."""
    user_id = message.from_user.id
    
    # Cache user info
    user_service = container.user_service()
    user_service.cache_user_info(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Share Existing Account", callback_data="share_existing")],
        [InlineKeyboardButton(text="🔐 Request New Authentication", callback_data="request_auth")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")]
    ])
    
    await message.reply(
        "🤝 **Account Sharing Options**\n\n"
        "**Share Existing Account:** Share one of your connected accounts\n"
        "**Request New Authentication:** Ask someone to authenticate a new account for you\n\n"
        "What would you like to do?",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await state.set_state(SharingState.selecting_share_type)

@router.callback_query(lambda c: c.data == "request_auth")
async def handle_request_auth(callback_query: CallbackQuery, state: FSMContext):
    """Handle authentication request flow."""
    # Show platform selection
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Todoist", callback_data="auth_platform_todoist")],
        [InlineKeyboardButton(text="📋 Trello", callback_data="auth_platform_trello")],
        [InlineKeyboardButton(text="📅 Google Calendar", callback_data="auth_platform_google_calendar")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_sharing")]
    ])
    
    await callback_query.message.edit_text(
        "🔐 **Request Authentication**\n\n"
        "Select the platform you want someone to authenticate for you:",
        reply_markup=keyboard,
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.selecting_platform)

@router.callback_query(lambda c: c.data.startswith("auth_platform_"))
async def handle_auth_platform_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle platform selection for auth request."""
    platform_type = callback_query.data.replace("auth_platform_", "")
    await state.update_data(platform_type=platform_type)
    
    await callback_query.message.edit_text(
        f"📝 **Name Your {platform_type.title()} Account**\n\n"
        f"Enter a name for this account (e.g., 'Work {platform_type.title()}', 'Personal Tasks'):",
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.waiting_for_account_name)

@router.message(AuthRequestState.waiting_for_account_name)
async def handle_account_name_input(message: Message, state: FSMContext):
    """Handle account name input."""
    account_name = message.text.strip()
    
    if len(account_name) < 3 or len(account_name) > 50:
        await message.reply("❌ Account name must be between 3 and 50 characters.")
        return
    
    await state.update_data(account_name=account_name)
    
    await message.reply(
        "👤 **Enter Username**\n\n"
        "Enter the Telegram username (with or without @) of the person who will authenticate this account:\n\n"
        "⚠️ They must have used this bot before.",
        parse_mode='Markdown'
    )
    await state.set_state(AuthRequestState.waiting_for_target_username)

@router.message(AuthRequestState.waiting_for_target_username)
async def handle_auth_target_username(message: Message, state: FSMContext):
    """Handle target username for auth request."""
    username = message.text.strip().lstrip('@')
    
    if not username or len(username) < 3:
        await message.reply("❌ Please enter a valid username.")
        return
    
    try:
        state_data = await state.get_data()
        platform_type = state_data['platform_type']
        account_name = state_data['account_name']
        
        # Create auth request
        sharing_service = container.sharing_service()
        auth_request_id = sharing_service.create_auth_request(
            requester_user_id=message.from_user.id,
            target_username=username,
            platform_type=platform_type,
            recipient_name=account_name
        )
        
        # Send notification to target user
        await send_auth_request_notification(auth_request_id, message.from_user.first_name)
        
        await message.reply(
            f"✅ **Authentication Request Sent!**\n\n"
            f"📤 **Sent to:** @{username}\n"
            f"📋 **Account:** {account_name} ({platform_type.title()})\n"
            f"⏰ **Expires in:** 24 hours\n\n"
            f"They will receive instructions to authenticate the account.\n"
            f"You'll be notified when complete.\n\n"
            f"Use /requests to view pending requests.",
            parse_mode='Markdown'
        )
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error creating auth request: {e}")
        await message.reply(f"❌ **Error:** {str(e)}")
        await state.clear()

async def send_auth_request_notification(auth_request_id: int, requester_name: str):
    """Send authentication request notification."""
    try:
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request:
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Authenticate Now", callback_data=f"auth_request_{auth_request_id}")],
            [InlineKeyboardButton(text="❌ Decline", callback_data=f"decline_auth_{auth_request_id}")]
        ])
        
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=auth_request.target_user_id,
            text=f"🔐 **Authentication Request**\n\n"
                 f"**From:** {requester_name}\n"
                 f"**Platform:** {auth_request.platform_type.title()}\n"
                 f"**Account Name:** {auth_request.recipient_name}\n"
                 f"**Expires:** In 24 hours\n\n"
                 f"{requester_name} is asking you to authenticate a {auth_request.platform_type.title()} account for them.\n\n"
                 f"If you accept, you'll go through the normal account setup process, but the account will be added to their recipients list instead of yours.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error sending auth request notification: {e}")

@router.callback_query(lambda c: c.data.startswith("auth_request_"))
async def handle_auth_request_acceptance(callback_query: CallbackQuery, state: FSMContext):
    """Handle authentication request acceptance."""
    try:
        auth_request_id = int(callback_query.data.replace("auth_request_", ""))
        user_id = callback_query.from_user.id
        
        # Validate request
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request or auth_request.target_user_id != user_id:
            await callback_query.answer("❌ Invalid request.", show_alert=True)
            return
        
        if not auth_request.is_active():
            await callback_query.message.edit_text(
                "❌ **Request Expired**\n\n"
                "This authentication request has expired or is no longer active."
            )
            return
        
        # Store auth request ID in state for completion
        await state.update_data(auth_request_id=auth_request_id)
        
        # Start platform-specific authentication flow
        platform_type = auth_request.platform_type
        
        if platform_type == 'google_calendar':
            # Start OAuth flow
            google_oauth_service = container.google_oauth_service()
            oauth_url = google_oauth_service.get_authorization_url(user_id)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Open Google Authorization", url=oauth_url)],
                [InlineKeyboardButton(text="📝 I Have the Code", callback_data="enter_auth_code_for_request")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data=f"cancel_auth_{auth_request_id}")]
            ])
            
            await callback_query.message.edit_text(
                f"🔐 **Authenticate {auth_request.recipient_name}**\n\n"
                f"You're authenticating this account for {auth_request.requester_user_id}\n\n"
                "1. Click 'Open Google Authorization'\n"
                "2. Sign in and grant permissions\n"
                "3. Copy the authorization code\n"
                "4. Click 'I Have the Code' and paste it\n\n"
                "⚠️ The account will be added to their recipients, not yours.",
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            await state.set_state(AuthRequestState.waiting_for_oauth)
            
        else:
            # For other platforms, ask for credentials directly
            await callback_query.message.edit_text(
                f"🔑 **Authenticate {auth_request.recipient_name}**\n\n"
                f"Enter your {platform_type.title()} credentials:\n\n"
                f"{get_platform_credential_instructions(platform_type)}\n\n"
                "⚠️ The account will be added to their recipients, not yours.",
                parse_mode='Markdown'
            )
            await state.set_state(AuthRequestState.waiting_for_credentials)
            
    except Exception as e:
        logger.error(f"Error handling auth request: {e}")
        await callback_query.answer("❌ Error processing request.", show_alert=True)

@router.message(AuthRequestState.waiting_for_credentials)
async def handle_auth_credentials_input(message: Message, state: FSMContext):
    """Handle credential input for auth request."""
    credentials = message.text.strip()
    
    try:
        state_data = await state.get_data()
        auth_request_id = state_data['auth_request_id']
        
        # Get auth request
        repository = container.unified_recipient_repository()
        auth_request = repository.get_auth_request_by_id(auth_request_id)
        
        if not auth_request:
            await message.reply("❌ Authentication request not found.")
            await state.clear()
            return
        
        # Complete the auth request
        sharing_service = container.sharing_service()
        recipient_id = sharing_service.complete_auth_request(
            auth_request_id=auth_request_id,
            target_user_id=message.from_user.id,
            credentials=credentials
        )
        
        # Notify requester
        await notify_auth_request_completed(auth_request, message.from_user.first_name)
        
        await message.reply(
            "✅ **Authentication Completed!**\n\n"
            f"The {auth_request.platform_type.title()} account has been authenticated and added to {auth_request.requester_user_id}'s recipients.\n\n"
            "Thank you for helping!",
            parse_mode='Markdown'
        )
        
        # Delete credential message for security
        await message.delete()
        await state.clear()
        
    except Exception as e:
        logger.error(f"Error completing auth request: {e}")
        await message.reply(f"❌ **Error:** {str(e)}")

async def notify_auth_request_completed(auth_request: AuthRequest, authenticator_name: str):
    """Notify requester that authentication is complete."""
    try:
        from core.container import container
        bot = container.bot()
        
        await bot.send_message(
            chat_id=auth_request.requester_user_id,
            text=f"✅ **Authentication Completed!**\n\n"
                 f"**Account:** {auth_request.recipient_name} ({auth_request.platform_type.title()})\n"
                 f"**Authenticated by:** {authenticator_name}\n\n"
                 f"The account has been added to your recipients.\n"
                 f"You can now use it to create tasks!\n\n"
                 f"Check /recipients to see your new account.",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error notifying auth completion: {e}")

@router.message(Command("requests"))
async def handle_requests_command(message: Message):
    """Show pending authentication requests."""
    user_id = message.from_user.id
    
    try:
        sharing_service = container.sharing_service()
        
        # Get requests where user is requester or target
        repository = container.unified_recipient_repository()
        sent_requests = repository.get_auth_requests_by_requester(user_id)
        received_requests = repository.get_pending_auth_requests_for_user(user_id)
        
        if not sent_requests and not received_requests:
            await message.reply(
                "📋 **No Authentication Requests**\n\n"
                "You don't have any pending authentication requests.",
                parse_mode='Markdown'
            )
            return
        
        text = "📋 **Authentication Requests**\n\n"
        keyboard = []
        
        if received_requests:
            text += "**📥 Received Requests:**\n"
            for req in received_requests:
                requester_info = container.user_service().get_user_info(req.requester_user_id)
                requester_name = requester_info.get('first_name', f'User{req.requester_user_id}') if requester_info else f'User{req.requester_user_id}'
                
                text += f"• {req.platform_type.title()} - {req.recipient_name}\n"
                text += f"  From: {requester_name}\n"
                text += f"  Expires: {req.expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"🔐 Authenticate: {req.recipient_name[:20]}",
                        callback_data=f"auth_request_{req.id}"
                    )
                ])
        
        if sent_requests:
            text += "\n**📤 Sent Requests:**\n"
            for req in sent_requests:
                if req.status == 'pending':
                    target_info = container.user_service().get_user_info(req.target_user_id)
                    target_name = target_info.get('first_name', f'User{req.target_user_id}') if target_info else f'User{req.target_user_id}'
                    
                    text += f"• {req.platform_type.title()} - {req.recipient_name}\n"
                    text += f"  To: {target_name}\n"
                    text += f"  Status: {req.status.title()}\n"
                    text += f"  Expires: {req.expires_at.strftime('%Y-%m-%d %H:%M')}\n\n"
                    
                    keyboard.append([
                        InlineKeyboardButton(
                            text=f"❌ Cancel: {req.recipient_name[:20]}",
                            callback_data=f"cancel_auth_req_{req.id}"
                        )
                    ])
        
        await message.reply(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing requests: {e}")
        await message.reply("❌ Error loading requests.")

def get_platform_credential_instructions(platform_type: str) -> str:
    """Get platform-specific credential instructions."""
    instructions = {
        'todoist': "Enter your Todoist API token:\n\n1. Go to Todoist Settings\n2. Navigate to Integrations\n3. Copy your API token",
        'trello': "Enter your Trello API key and token (separated by colon):\n\nFormat: YOUR_API_KEY:YOUR_TOKEN\n\n1. Get API key from trello.com/app-key\n2. Generate token using the link on that page"
    }
    return instructions.get(platform_type, "Enter your credentials for this platform")
```

### Step 3.4: Repository Methods

Add to `database/unified_recipient_repository.py`:

```python
def create_auth_request(self, requester_user_id: int, target_user_id: int,
                       platform_type: str, recipient_name: str, expires_at: datetime) -> int:
    """Create new authentication request."""
    try:
        query = """
        INSERT INTO auth_requests 
        (requester_user_id, target_user_id, platform_type, recipient_name, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """
        cursor = self.connection.execute(query, 
            (requester_user_id, target_user_id, platform_type, recipient_name, expires_at))
        
        auth_request_id = cursor.lastrowid
        self.connection.commit()
        
        logger.info(f"Created auth request {auth_request_id}")
        return auth_request_id
        
    except sqlite3.Error as e:
        logger.error(f"Error creating auth request: {e}")
        raise

def get_auth_request_by_id(self, auth_request_id: int) -> Optional[AuthRequest]:
    """Get authentication request by ID."""
    query = """
    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
           status, expires_at, completed_recipient_id, created_at, updated_at
    FROM auth_requests
    WHERE id = ?
    """
    
    cursor = self.connection.execute(query, (auth_request_id,))
    row = cursor.fetchone()
    
    if row:
        return AuthRequest(
            id=row[0],
            requester_user_id=row[1],
            target_user_id=row[2],
            platform_type=row[3],
            recipient_name=row[4],
            status=row[5],
            expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
            completed_recipient_id=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            updated_at=datetime.fromisoformat(row[9]) if row[9] else None
        )
    
    return None

def get_pending_auth_requests_for_user(self, user_id: int) -> List[AuthRequest]:
    """Get pending authentication requests targeting a user."""
    query = """
    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
           status, expires_at, completed_recipient_id, created_at, updated_at
    FROM auth_requests
    WHERE target_user_id = ? AND status = 'pending' AND expires_at > ?
    ORDER BY created_at DESC
    """
    
    cursor = self.connection.execute(query, (user_id, datetime.utcnow()))
    
    requests = []
    for row in cursor:
        requests.append(AuthRequest(
            id=row[0],
            requester_user_id=row[1],
            target_user_id=row[2],
            platform_type=row[3],
            recipient_name=row[4],
            status=row[5],
            expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
            completed_recipient_id=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            updated_at=datetime.fromisoformat(row[9]) if row[9] else None
        ))
    
    return requests

def update_auth_request_status(self, auth_request_id: int, status: str, 
                             completed_recipient_id: int = None) -> bool:
    """Update authentication request status."""
    try:
        if completed_recipient_id:
            query = """
            UPDATE auth_requests 
            SET status = ?, completed_recipient_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """
            cursor = self.connection.execute(query, (status, completed_recipient_id, auth_request_id))
        else:
            query = """
            UPDATE auth_requests 
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """
            cursor = self.connection.execute(query, (status, auth_request_id))
        
        success = cursor.rowcount > 0
        self.connection.commit()
        
        return success
        
    except sqlite3.Error as e:
        logger.error(f"Error updating auth request status: {e}")
        return False

def cleanup_expired_auth_requests(self) -> int:
    """Mark expired auth requests as expired."""
    try:
        query = """
        UPDATE auth_requests 
        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
        WHERE status = 'pending' AND expires_at < ?
        """
        cursor = self.connection.execute(query, (datetime.utcnow(),))
        count = cursor.rowcount
        self.connection.commit()
        
        return count
        
    except sqlite3.Error as e:
        logger.error(f"Error cleaning up expired auth requests: {e}")
        return 0

def get_auth_requests_by_requester(self, requester_user_id: int) -> List[AuthRequest]:
    """Get all auth requests created by a user."""
    query = """
    SELECT id, requester_user_id, target_user_id, platform_type, recipient_name,
           status, expires_at, completed_recipient_id, created_at, updated_at
    FROM auth_requests
    WHERE requester_user_id = ?
    ORDER BY created_at DESC
    """
    
    cursor = self.connection.execute(query, (requester_user_id,))
    
    requests = []
    for row in cursor:
        requests.append(AuthRequest(
            id=row[0],
            requester_user_id=row[1],
            target_user_id=row[2],
            platform_type=row[3],
            recipient_name=row[4],
            status=row[5],
            expires_at=datetime.fromisoformat(row[6]) if row[6] else None,
            completed_recipient_id=row[7],
            created_at=datetime.fromisoformat(row[8]) if row[8] else None,
            updated_at=datetime.fromisoformat(row[9]) if row[9] else None
        ))
    
    return requests
```

---

---

## Phase 4: Testing Integration

### Step 4.1: Add Google Calendar Tests

**File:** `tests/unit/test_google_calendar_platform.py`

```python
# tests/unit/test_google_calendar_platform.py
import json
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from platforms.google_calendar import GoogleCalendarPlatform
from core.exceptions import PlatformError, OAuthError

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

@patch('platforms.google_calendar.build')
@patch('platforms.google_calendar.Credentials')
def test_google_calendar_create_task_success(mock_credentials_class, mock_build, mock_credentials):
    """Test successful Google Calendar task creation."""
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
    
    task_data = {
        'title': 'Test Calendar Event',
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
    assert event['summary'] == 'Test Calendar Event'
    assert event['description'] == 'Test Description'
    assert 'start' in event
    assert 'end' in event
```

**File:** `tests/integration/test_auth_request_workflow.py`

```python
# tests/integration/test_auth_request_workflow.py
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock

from services.sharing_service import SharingService
from models.auth_request import AuthRequest

@pytest.fixture
def mock_sharing_service():
    mock_repo = Mock()
    mock_user_service = Mock()
    return SharingService(mock_repo, mock_user_service)

async def test_complete_auth_request_workflow(mock_sharing_service):
    """Test complete authentication request workflow."""
    # Step 1: Create auth request
    mock_sharing_service.user_service.get_user_id_from_username.return_value = 200
    mock_sharing_service.repository.create_auth_request.return_value = 1
    
    auth_request_id = mock_sharing_service.create_auth_request(
        requester_user_id=100,
        target_username="test_user",
        platform_type="google_calendar", 
        recipient_name="Test Calendar"
    )
    
    assert auth_request_id == 1
    
    # Step 2: Simulate auth request acceptance
    auth_request = AuthRequest(
        id=1,
        requester_user_id=100,
        target_user_id=200,
        platform_type="google_calendar",
        recipient_name="Test Calendar",
        status='pending',
        expires_at=datetime.utcnow() + timedelta(hours=1)
    )
    
    mock_sharing_service.repository.get_auth_request_by_id.return_value = auth_request
    mock_sharing_service.repository.add_personal_recipient.return_value = 1
    mock_sharing_service.repository.update_auth_request_status.return_value = True
    
    # Step 3: Complete authentication
    recipient_id = mock_sharing_service.complete_auth_request(
        auth_request_id=1,
        target_user_id=200,
        credentials='{"token": "test_token"}',
        platform_config='{"calendar_id": "primary"}'
    )
    
    assert recipient_id == 1
    
    # Verify recipient created for requester (not target)
    mock_sharing_service.repository.add_personal_recipient.assert_called_with(
        user_id=100,  # Requester gets the account
        name="Test Calendar",
        platform_type="google_calendar",
        credentials='{"token": "test_token"}',
        platform_config='{"calendar_id": "primary"}'
    )
```

### Step 4.2: Run Tests with Existing Infrastructure

Use the existing test runner with new tests:

```bash
# Run all unit tests including new Google Calendar tests
./run-tests.sh unit

# Run specific Google Calendar tests
./run-tests.sh unit test_google_calendar_platform.py

# Run auth request workflow tests
./run-tests.sh integration test_auth_request_workflow.py

# Run sharing security tests
./run-tests.sh unit test_sharing_security.py

# Run comprehensive test suite
./run-tests.sh all
```

### Step 4.3: Integration with Existing Test Infrastructure

The new tests integrate seamlessly with the existing Docker-based test runner:

- **Environment Consistency**: Tests use same Docker environment as production
- **Test Isolation**: Each test gets fresh database state
- **Mock Services**: External APIs properly mocked for unit tests
- **Factory Integration**: Uses existing Factory Boy patterns for test data

---

## Phase 5: Deployment & Migration

### Step 5.1: Database Migration

The new schema is designed to be deployed from scratch without leaving any legacy residue:

```bash
# Deploy with clean database migration
docker-compose up -d --build

# The migration system automatically creates all tables from schema.sql
# No legacy structures or manual cleanup needed
```

### Step 5.2: Configuration Validation

Add validation to ensure clean deployment:

```python
# config.py
@classmethod
def validate(cls) -> None:
    """Validate configuration for Google Calendar integration."""
    # Existing validation...
    
    # Google Calendar validation
    if cls.GOOGLE_CLIENT_ID and not cls.GOOGLE_CLIENT_SECRET:
        raise ValueError("GOOGLE_CLIENT_SECRET required when GOOGLE_CLIENT_ID is set")
    
    if cls.GOOGLE_CLIENT_SECRET and not cls.GOOGLE_CLIENT_ID:
        raise ValueError("GOOGLE_CLIENT_ID required when GOOGLE_CLIENT_SECRET is set")
    
    # Platform support validation
    if 'google_calendar' in cls.SUPPORTED_PLATFORMS and not cls.GOOGLE_CLIENT_ID:
        logger.warning("Google Calendar enabled in SUPPORTED_PLATFORMS but credentials not configured")
```

### Step 5.3: Production Deployment Checklist

- [ ] **Environment Variables**: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` set
- [ ] **Database Schema**: Clean deployment with new schema from scratch
- [ ] **Docker Build**: Updated environment.yml with Google Calendar dependencies  
- [ ] **Test Suite**: All tests passing with `./run-tests.sh all`
- [ ] **Security Review**: Credential resolution logic tested and verified
- [ ] **Monitoring**: Logs configured for OAuth token refresh events

---

## Architecture Benefits

1. **Clean Separation of Concerns**
   - OAuth logic isolated in dedicated service
   - Platform implementations follow consistent interface
   - Sharing logic separated from authentication

2. **Security First**
   - Credentials never exposed to grantees
   - Authorization-based pointer system
   - Automatic cleanup with CASCADE DELETE

3. **Scalable Design**
   - Integrates with existing Docker infrastructure
   - Clean migration system deployable from scratch
   - Extensible platform architecture

4. **Complete User Workflows**
   - Two sharing modes: existing accounts and auth requests
   - Comprehensive error handling
   - Corner cases addressed (expired requests, cancellations)

5. **Production Ready**
   - Integrates with existing test infrastructure
   - Comprehensive logging and monitoring
   - Clean database schema with proper constraints

This architecture provides a solid foundation for adding Google Calendar integration and secure account sharing while maintaining clean code principles and security best practices. The implementation integrates seamlessly with the existing TGHandyUtils infrastructure and deployment patterns.