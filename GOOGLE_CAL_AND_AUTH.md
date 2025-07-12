# Engineering Blueprint: Google Calendar & Secure Account Sharing

This document provides a detailed, step-by-step implementation plan for integrating Google Calendar as a new platform and adding a secure account sharing workflow. Follow these steps precisely to ensure a high-quality, secure, and testable implementation.

---

## Phase 0: Project Setup & Pre-flight Checks

**Objective:** Prepare the development environment and add all necessary dependencies.

- [ ] **Step 0.1: Update Dependencies**
    - **Action:** Add the following lines to `requirements.txt`:
      ```
      google-api-python-client==2.88.0
      google-auth-httplib2==0.1.0
      google-auth-oauthlib==1.0.0
      aiohttp==3.9.1
      ```
    - **Action:** Install the new dependencies.
    - **Command:** `pip install -r requirements.txt`

- [ ] **Step 0.2: Google Cloud Project Setup & OAuth Configuration**
    - **Action:** In the Google Cloud Console, create a project and enable the **Google Calendar API**.
    - **Action:** Create **OAuth 2.0 Client ID** credentials. **Important:** Choose the appropriate credential type based on your deployment scenario (see OAuth Implementation Options below).
    - **Action:** Add the credentials to your environment configuration (`env.sample` and your local `.env` file).

### OAuth Configuration

**For private bots without public domains:**

- **Google Console Setup:** 
  - Credential Type: **Desktop Application** 
  - No redirect URI needed (uses Google's out-of-band flow)
- **Environment Variables:**
  ```
  GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
  GOOGLE_CLIENT_SECRET="your-client-secret"
  ```
- **User Flow:** User opens Google auth URL → authorizes → copies code → pastes in bot

---

## Phase 1: Google Calendar Provider Integration

### Part A: Backend Core Logic & Services

**Objective:** Implement the non-UI backend logic for Google Calendar, ensuring it is fully unit-tested before connecting it to the bot.

- [ ] **Step 1.1: Create the Google OAuth Service**
    - **File:** Create `services/google_oauth_service.py`.
    - **Why:** This service encapsulates all logic related to the OAuth2 flow for manual code entry.
    - **Action:** Implement the `GoogleOAuthService` class.
      ```python
      # services/google_oauth_service.py
      import json
      from typing import Optional
      from google_auth_oauthlib.flow import Flow
      from core.container import container

      class GoogleOAuthService:
          def __init__(self, client_secrets_config: dict):
              self.client_secrets_config = client_secrets_config
              self.scopes = ['https://www.googleapis.com/auth/calendar']

          def get_authorization_url(self, user_id: int) -> str:
              """Generate OAuth URL for manual code entry."""
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
              return authorization_url

          def exchange_code_for_token(self, code: str) -> str:
              """Exchange authorization code for credentials."""
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
                  'scopes': flow.credentials.scopes
              }
              return json.dumps(credentials)
      ```
    - **Action:** Register this service in the dependency injection container (`core/container.py`).

- [ ] **Step 1.2: Create the Google Calendar Platform Implementation**
    - **File:** Create `platforms/google_calendar.py`.
    - **Why:** This class adapts the Google Calendar API to the application's `AbstractTaskPlatform` interface, allowing the rest of the app to interact with it generically.
    - **Action:** Implement the `GoogleCalendarPlatform` class.
      ```python
      # platforms/google_calendar.py
      import json
      from typing import Dict, Any, Optional
      from google.oauth2.credentials import Credentials
      from googleapiclient.discovery import build
      
      from platforms.base import AbstractTaskPlatform, register_platform

      @register_platform('google_calendar')
      class GoogleCalendarPlatform(AbstractTaskPlatform):
          def __init__(self, credentials_json: str):
              # **Risk Mitigation:** Credentials are created per-instance and not stored
              # globally, reducing the risk of credential leakage between users.
              creds_data = json.loads(credentials_json)
              self.credentials = Credentials(**creds_data)
              self.service = build('calendar', 'v3', credentials=self.credentials)

          def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
              # Implementation to create a calendar event
              # Ensure proper timezone handling!
              pass
          
          # ... implement all other abstract methods ...

          @staticmethod
          def is_configured_static(platform_settings: Dict[str, Any]) -> bool:
              """Checks if the platform is configured without instantiation."""
              # **Risk Mitigation:** Checks for token validity before attempting API calls.
              # A more robust check would be to see if a refresh token exists.
              credentials_str = platform_settings.get('google_calendar_credentials')
              if not credentials_str:
                  return False
              try:
                  creds = json.loads(credentials_str)
                  return 'refresh_token' in creds and creds['refresh_token'] is not None
              except (json.JSONDecodeError, KeyError):
                  return False
      ```
    - **Action:** Add `from . import google_calendar` to `platforms/__init__.py`.

- [ ] **Step 1.3: Unit Test the Backend Logic**
    - **Why:** Unit testing now ensures the core business logic is correct and secure *before* it's connected to the live user interface, preventing bugs and regressions.
    - **Action:** Create `tests/unit/test_google_oauth_service.py` and `tests/unit/test_google_calendar_platform.py`.
    - **Action:** Write tests mocking `google_auth_oauthlib.flow` and `googleapiclient.discovery.build` to test your services in isolation.
    - **Action:** Verify that the `state` parameter is correctly set in the auth URL. Test the credential parsing and error handling in the platform class.
    - **Command:** Run the tests for this part.
      ```bash
      pytest tests/unit/test_google_oauth_service.py tests/unit/test_google_calendar_platform.py
      ```
    - **Instruction:** **Do not proceed until all tests pass.**

### Part B: UI, State Management, and Integration

**Objective:** Connect the backend services to the Telegram bot UI, creating the user-facing authentication flow.

- [ ] **Step 1.4: Implement OAuth Flow Based on Selected Method**
    
    **For Manual Code Entry or QR Code Methods (Recommended):**
    - **File:** No separate server needed - handled entirely in bot handlers
    - **Why:** Users manually enter the authorization code, eliminating need for redirect handling
    
    **For Webhook Method (Public Domain Required):**
    - **File:** Create `oauth_server.py` (embedded in main bot process)
    - **Why:** Handles OAuth redirects when bot has public domain or ngrok tunnel
    - **Action:** Implement embedded aiohttp server:
      ```python
      # oauth_server.py
      from aiohttp import web
      import asyncio
      from core.container import container

      async def start_oauth_server():
          """Start OAuth callback server as part of main process."""
          app = web.Application()
          app.router.add_get('/oauth/callback', handle_oauth_callback)
          
          runner = web.AppRunner(app)
          await runner.setup()
          site = web.TCPSite(runner, 'localhost', 8080)
          await site.start()
          
          logger.info("OAuth server started on http://localhost:8080")
          
          # Keep server running
          while True:
              await asyncio.sleep(1)

      async def handle_oauth_callback(request):
          """Handle OAuth callback and update database."""
          code = request.query.get('code')
          state = request.query.get('state')
          
          if not code or not state:
              return web.Response(text="Invalid OAuth callback", status=400)
          
          try:
              # Store OAuth code for bot to process
              oauth_state_manager = container.oauth_state_manager()
              user_id = oauth_state_manager.complete_oauth_request(state, code)
              
              if user_id:
                  # Notify user in Telegram
                  await notify_user_oauth_complete(user_id)
                  return web.Response(text="✅ Authentication successful! Return to Telegram.")
              else:
                  return web.Response(text="❌ Invalid or expired authentication request.", status=400)
                  
          except Exception as e:
              logger.error(f"OAuth callback error: {e}")
              return web.Response(text="❌ Authentication failed. Please try again.", status=500)
      ```
    - **Action:** Update `main.py` to start OAuth server conditionally:
      ```python
      async def main():
          config = container.config()
          oauth_task = None
          
          if config.GOOGLE_OAUTH_METHOD == "webhook":
              oauth_task = asyncio.create_task(start_oauth_server())
              
          tasks = [dp.start_polling(bot), scheduler_task]
          if oauth_task:
              tasks.append(oauth_task)
              
          await asyncio.gather(*tasks)
      ```

- [ ] **Step 1.5: Implement the UI and State Machine**
    - **File:** Modify `handlers_modular/states/recipient_setup.py` and the relevant handler files.
    - **Why:** This integrates the OAuth flow into the existing state machine for adding a new account.
    - **Action:** Implement manual code entry flow:
    
    ```python
    @router.callback_query(lambda c: c.data == "platform_type_google_calendar")
    async def handle_google_calendar_setup(callback_query: CallbackQuery, state: FSMContext):
        """Handle Google Calendar setup with manual code entry."""
        await state.update_data(platform_type="google_calendar")
        
        user_id = callback_query.from_user.id
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
            "2. Sign in and grant permissions\n"
            "3. **Copy the authorization code** from the final page\n"
            "4. Return here and click 'I Have the Code'\n"
            "5. Paste the code when prompted\n\n"
            "🔒 The code is only used once to get secure tokens.",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        await state.set_state(RecipientState.waiting_for_oauth_code)
    
    @router.callback_query(lambda c: c.data == "enter_auth_code")
    async def handle_enter_auth_code(callback_query: CallbackQuery, state: FSMContext):
        """Prompt user to enter authorization code."""
        await callback_query.message.edit_text(
            "📝 **Enter Authorization Code**\n\n"
            "Paste the authorization code you received from Google:"
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
                "You can now create calendar events using this bot.",
                reply_markup=get_recipient_management_keyboard()
            )
            await state.clear()
            
            # Delete the message containing the auth code for security
            await message.delete()
            
        except Exception as e:
            logger.error(f"OAuth code exchange error: {e}")
            await message.reply(
                "❌ **Authorization Failed**\n\n"
                "The code appears to be invalid or expired.\n"
                "Please try the setup process again."
            )
    ```

- [ ] **Step 1.6: Integration Test the Full Flow**
    - **Why:** To ensure all components (UI, state machine, OAuth service, platform class) work together correctly.
    - **Action:** Create `tests/integration/test_google_calendar_flow.py`.
    - **Action:** In this test, you will need to simulate a user's conversation. You will mock the `GoogleOAuthService` to avoid making real web requests, but you will test the full state machine logic from start to finish.
    - **Command:** Run the integration test.
      ```bash
      pytest tests/integration/test_google_calendar_flow.py
      ```
    - **Instruction:** **Do not proceed until the full flow is tested and working.**

---

## Phase 2: Secure Account Sharing

### Part A: Database & Core Logic

**Objective:** Build the secure foundation for account sharing. The logic implemented here is security-critical.

- [ ] **Step 2.1: Extend the Database Schema**
    - **File:** Create a new migration script in `database/migrations/`.
    - **Why:** These schema changes create the necessary relationships to track sharing permissions without storing redundant, insecure data. The `ON DELETE CASCADE` is a critical part of the design for data integrity.
    - **Action:** Add the following SQL to your migration script.
      ```sql
      -- Create the table to track sharing authorizations
      CREATE TABLE shared_authorizations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          owner_user_id INTEGER NOT NULL,
          grantee_user_id INTEGER NOT NULL,
          owner_recipient_id INTEGER NOT NULL,
          status TEXT NOT NULL, -- 'pending', 'accepted', 'revoked', 'declined'
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (owner_recipient_id) REFERENCES recipients (id) ON DELETE CASCADE
      );

      -- Add a column to the recipients table to link to an authorization
      ALTER TABLE recipients ADD COLUMN shared_authorization_id INTEGER NULL;
      ```
    - **Action:** Run the migration to apply the changes to your database.

- [ ] **Step 2.2: Update Repository and Models**
    - **File:** `models/unified_recipient.py` and `database/unified_recipient_repository.py`.
    - **Why:** To create a data access layer for the new tables.
    - **Action:** Create a `SharedAuthorization` dataclass in the models file.
    - **Action:** Add new methods to `UnifiedRecipientRepository` to `create_authorization`, `update_authorization_status`, `get_pending_authorizations_for_user`, and `get_active_shares_by_owner`.

- [ ] **Step 2.3: Implement Secure Credential Resolution**
    - **File:** Modify the service responsible for creating platform instances (e.g., a `PlatformFactory` or a service that uses it).
    - **Why:** **This is the most critical security step.** The logic must ensure that a Grantee *never* has access to the Owner's raw credentials. The shared recipient record acts only as a secure pointer.
    - **Action:** Modify the logic that instantiates a platform.
      ```python
      # Simplified example of the modified logic
      def get_platform_for_recipient(recipient: UnifiedRecipient) -> AbstractTaskPlatform:
          # If it's a shared recipient, resolve the real credentials
          if recipient.shared_authorization_id is not None:
              # 1. Fetch the authorization record
              auth = repository.get_authorization_by_id(recipient.shared_authorization_id)
              # 2. Fetch the OWNER's recipient record
              owner_recipient = repository.get_recipient_by_id(auth.owner_user_id, auth.owner_recipient_id)
              # 3. Use the OWNER's credentials to instantiate the platform
              credentials = owner_recipient.credentials
          else:
              # It's a personal recipient, use their own credentials
              credentials = recipient.credentials
          
          # Now, create the platform instance with the resolved credentials
          return TaskPlatformFactory.get_platform(recipient.platform_type, credentials)
      ```

- [ ] **Step 2.4: Unit Test the Core Sharing Logic**
    - **Why:** To rigorously verify the security and correctness of the database and credential resolution logic.
    - **Action:** Create `tests/unit/test_sharing_logic.py`.
    - **Action:**
        - Test the new repository methods for creating and updating authorizations.
        - **Crucially, write tests for the credential resolution logic.** Create a mock repository, simulate a Grantee's recipient and an Owner's recipient, and assert that the platform is always instantiated with the *Owner's* credentials.
        - Write a test to simulate an Owner deleting their recipient and assert that the `ON DELETE CASCADE` correctly removes the associated `shared_authorizations` record.
    - **Command:**
      ```bash
      pytest tests/unit/test_sharing_logic.py
      ```
    - **Instruction:** **Do not proceed until all security logic is verified by tests.**

### Part B: User Workflows & Final Testing

**Objective:** Implement the user-facing UI for sharing, accepting, and revoking access, and perform final end-to-end testing.

- [ ] **Step 2.5: Implement the Sharer (Owner) Workflow**
    - **File:** Create new handlers in `handlers_modular/commands/` for a `/share` command.
    - **Action:** Use `FSMContext` to guide the Owner through selecting one of their personal accounts and providing the Telegram `@username` of the Grantee. Create a `pending` authorization in the database and send a notification to the Grantee.

- [ ] **Step 2.6: Implement the Grantee Workflow**
    - **File:** Create a new callback handler in `handlers_modular/callbacks/`.
    - **Action:** The notification message to the Grantee must have "Accept" and "Decline" buttons with the `authorization_id` in the callback data.
    - **Action:** On "Accept", update the authorization status and create a new `UnifiedRecipient` for the Grantee with `is_personal=False` and the `shared_authorization_id` set.
    - **Action:** On "Decline", simply update the status. Notify the Owner in both cases.

- [ ] **Step 2.7: Implement Management and Revocation**
    - **File:** Create a `/sharing` command handler.
    - **Action:** This handler should display a list of active shares. Owners should see who they've shared with and have a "Revoke" button. Grantees should see what's been shared with them.
    - **Action:** The "Revoke" handler must update the authorization status to `revoked` and, critically, **delete the Grantee's corresponding `UnifiedRecipient` record** to immediately cut off access.

- [ ] **Step 2.8: Integration Test the Sharing Workflow**
    - **Why:** To ensure the complete user-facing workflow is functioning correctly.
    - **Action:** Create `tests/integration/test_sharing_workflow.py`.
    - **Action:** Simulate a full conversation between two users:
        1. User A (Owner) initiates a share with User B (Grantee).
        2. User B accepts the share.
        3. Verify User B now has a new recipient.
        4. Verify User B can use the shared recipient to create a task (mocking the platform call).
        5. User A revokes the share.
        6. Verify User B's shared recipient is deleted and they can no longer use it.
    - **Command:**
      ```bash
      pytest tests/integration/test_sharing_workflow.py
      ```

---

## Phase 3: Final System Review & Documentation

- [ ] **Step 3.1: End-to-End Manual Testing**
    - **Action:** Manually test the most critical combined workflow: Have User A authorize their Google Calendar account, then share it with User B. Verify User B can create a real event on User A's calendar.

- [ ] **Step 3.2: Security & UX Review**
    - **Action:** Review the entire implementation. Does any part of the UI leak information? Is the language clear? Is it obvious to a Grantee when they are using a shared account?
    - **Action:** Double-check that the `ON DELETE CASCADE` behavior was tested and is working as expected. This prevents orphaned data, which is a potential long-term bug.

- [ ] **Step 3.3: Update Documentation**
    - **Action:** Update `README.md` or other user-facing documentation.
    - **Action:** Explain how to set up Google Calendar, including the OAuth steps.
    - **Action:** Clearly document the account sharing feature from both the Owner's and Grantee's perspectives.
