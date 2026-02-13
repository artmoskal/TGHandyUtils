# 🏗️ TGHandyUtils Architecture

This document explains how TGHandyUtils is structured and how the different pieces work together. Written for humans who want to understand or contribute to the codebase!

## 📁 Project Structure

```
TGHandyUtils/
├── main.py                 # Entry point - starts the bot
├── core/                   # Core infrastructure
│   ├── container.py        # Dependency injection container
│   ├── interfaces.py       # Service interfaces & data structures
│   └── logging.py          # Centralized logging configuration
├── services/               # Business logic layer
│   ├── recipient_task_service.py   # Task creation & management
│   ├── recipient_service.py        # Recipient (platform) management
│   ├── parsing_service.py          # Natural language parsing
│   └── voice_processing.py         # Voice message transcription
├── database/               # Data access layer
│   ├── connection.py               # Database connection management
│   ├── repositories.py             # Task repository
│   ├── unified_recipient_repository.py  # Main recipient storage
│   ├── user_preferences_repository.py   # User settings
│   └── auth_request_repository.py       # Authentication requests
├── handlers_modular/       # Telegram bot handlers
│   ├── message/            # Message handlers (text, voice, photo)
│   ├── callbacks/          # Button callback handlers
│   └── commands/           # Bot commands (/start, /recipients, etc.)
├── platforms/              # Task platform integrations
│   ├── base.py             # Abstract platform interface
│   ├── todoist.py          # Todoist integration
│   ├── trello.py           # Trello integration
│   └── google_calendar.py  # Google Calendar integration
├── models/                 # Data models
│   ├── task.py             # Task data structures
│   ├── unified_recipient.py # Recipient (platform) models
│   └── parameter_objects.py # Request/response objects
└── helpers/                # Utility modules
    ├── error_messages.py   # Centralized error messages
    ├── constants.py        # HTTP timeouts, retries, etc.
    └── ui_helpers.py       # Telegram UI formatting
```

## 🔄 Request Flow

Here's what happens when you send a message to create a task:

### 1. **Message Reception** 
```
User sends: "Remind me to call doctor tomorrow at 3 PM"
    ↓
Telegram → main.py → MessageHandler
```

### 2. **Message Processing**
```
MessageHandler → ParsingService
    - Extracts: title="Call doctor"
    - Parses: due_time="tomorrow at 3 PM" → UTC timestamp
    - Handles timezone: User's location (e.g., "NY") → LLM determines UTC offset
    - Timezone-agnostic: LLM sees local time only, prevents confusion
```

### 3. **Task Creation**
```
MessageHandler → RecipientTaskService.create_task_for_recipients()
    - Creates TaskCreationRequest object (parameter object pattern)
    - Determines recipients (default platforms or user selection)
    - Stores task in database
    - Creates tasks on each platform (Todoist, Trello, etc.)
```

### 4. **Platform Integration**
```
RecipientTaskService → TodoistPlatform.create_task()
    - Uses HTTP_TIMEOUT from constants
    - Retries MAX_RETRIES times on failure
    - Returns task URL or error
```

### 5. **Response Generation**
```
RecipientTaskService → User
    - Creates TaskFeedbackData object
    - Formats success/error message
    - Shows task details in user's timezone
    - Provides action buttons if needed
```

## 🎯 Key Design Patterns

### 1. **Dependency Injection (DI)**
All services are wired through the DI container for testability:
```python
# core/container.py
container = ApplicationContainer()
container.wire(modules=[...])

# Usage in handlers
@inject
def handle_message(
    parsing_service: IParsingService = Provide[ApplicationContainer.parsing_service]
):
    # Service is automatically injected
```

### 2. **Repository Pattern**
Data access is abstracted through repositories:
```python
# Instead of direct database calls
task_id = db.execute("INSERT INTO tasks...")

# We use repositories
task_id = task_repository.create(user_id, task_data)
```

### 3. **ServiceResult Pattern**
No more tuple unpacking! Services return structured results:
```python
# Old way (confusing)
success, message, data = service.create_task(...)
if success:
    ...

# New way (clear)
result = service.create_task(...)
if result.success:
    print(result.message)
    use(result.data)
```

### 4. **Parameter Objects**
Complex method calls now use clear objects:
```python
# Old way (what does each parameter mean?)
service.create_task(12345, "Title", "Desc", None, [1,2,3], {...}, 0, 0)

# New way (self-documenting)
request = TaskCreationRequest(
    user_id=12345,
    title="Title", 
    description="Desc",
    specific_recipients=[1,2,3]
)
result = service.create_task(request)
```

## 🔌 Platform Integration

### Adding a New Platform
1. Create a new file in `platforms/`
2. Inherit from `AbstractTaskPlatform`
3. Implement required methods:
   ```python
   @register_platform('myplatform')
   class MyPlatform(AbstractTaskPlatform):
       def create_task(self, task_data): ...
       def delete_task(self, task_id): ...
       def get_task_url(self, task_id): ...
   ```
4. The factory will automatically recognize it!

## 🗄️ Database Schema

### Core Tables
- **users**: Telegram user information
- **tasks**: Created tasks with metadata  
- **recipients**: User's platforms (Todoist, Trello, etc.)
- **task_recipients**: Links tasks to platforms
- **user_preferences**: Timezone, notification settings

### Recipient System
- Personal recipients: Your own platform accounts
- Shared recipients: Others' accounts you can send tasks to
- Default recipients: Platforms that receive tasks automatically

## 🧪 Testing

### Running Tests

⚠️ **IMPORTANT**: Always use test.sh script!

```bash
# Run all tests
./test.sh all

# Run unit tests only
./test.sh unit

# Run integration tests (uses API)
./test.sh integration
```

### Test Organization
- `tests/unit/`: Unit tests for individual components
- `tests/integration/`: Tests for component interactions
- `tests/factories/`: Factory Boy factories for test data

## 🔐 Security & Multi-User

### User Isolation
- All queries filtered by user_id
- Foreign key constraints prevent cross-user access
- Each user's data completely isolated

### Credential Storage
- Platform tokens stored encrypted per user
- No credential sharing between users
- Secure token validation on each use

## 📊 Error Handling

### Centralized Error Messages
All user-facing errors in one place:
```python
# helpers/error_messages.py
class ErrorMessages:
    RECIPIENT_NOT_FOUND = "❌ Recipient not found"
    TASK_CREATION_FAILED = "❌ Failed to create task in database."
```

### Graceful Degradation
If one platform fails, others still work:
```
✅ Task created on:
• 📝 My Todoist
• ❌ Wife's Trello (API error)

Task saved locally and can be retried.
```

## 🚀 Performance Optimizations

### Connection Pooling
- SQLite with WAL mode for better concurrency
- Connection reuse through DatabaseManager

### Constant Values
- HTTP timeouts: 30 seconds
- Max retries: 3 attempts
- Backoff factor: 2.0 (exponential backoff)

## 🔄 Continuous Improvements

The codebase follows SOLID principles and clean architecture patterns, making it easy to:
- Add new platforms
- Implement new features
- Fix bugs without breaking existing functionality
- Test components in isolation

---

**Remember**: Good architecture is invisible when it works well. If you're confused about where something belongs, check the existing patterns or ask! 🚀