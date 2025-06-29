# TGHandyUtils - Codebase Reference

> **Generated**: 2025-06-29  
> **Purpose**: Quick reference for key files, classes, and non-obvious implementation details

## 🗂️ Key Files & Responsibilities

### 🚪 Entry Points

#### `main.py`
- **Purpose**: Application bootstrap and lifecycle management
- **Key Functions**:
  - `main()`: Async application entry point with scheduler coordination
- **Dependencies**: Initializes DI, database schema, scheduler
- **Non-obvious**: Uses `asyncio.create_task()` for background scheduler, proper cleanup on shutdown

#### `bot.py`  
- **Purpose**: Aiogram bot configuration and global instance management
- **Key Functions**:
  - `initialize_bot()`: Late initialization with config injection
- **Global State**: `bot`, `dp` (dispatcher), `router` - used across modules
- **Token Management**: Fallback to environment variables if config incomplete

### 🧠 Core Architecture

#### `core/container.py` - Main DI Container
```python
class ApplicationContainer(containers.DeclarativeContainer):
    # Singletons: Config, DatabaseManager  
    # Factories: Services, Repositories
```
- **Pattern**: Dependency Injector library with provider patterns
- **Scope**: Singleton for shared resources, Factory for stateful services
- **Non-obvious**: Container wiring happens at application startup via `initialization.py`

#### `core/recipient_container.py` - Secondary Container 🚨
```python
class RecipientContainer(containers.DeclarativeContainer):
    # Recipient-specific services
```
- **Issue**: Separate container adds complexity without clear benefits
- **Usage**: Provides `CleanRecipientService`, `CleanRecipientTaskService`
- **Recommendation**: Merge with main container

#### `core/interfaces.py` - Core Abstractions
```python
# Key Interfaces:
IConfig, IParsingService, ITaskRepository, ITaskPlatform
```
- **Pattern**: ABC (Abstract Base Classes) for interface contracts
- **Type Safety**: Full type hints, Optional returns for nullable operations
- **Coverage**: All major services have interface abstractions

#### `core/logging.py` - Centralized Logging
```python
def setup_logging():
    # File rotation: 10MB, 5 backups
    # UTF-8 encoding, detailed format
```
- **Format**: `%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s`
- **Handlers**: Both file and console output
- **Directory**: Auto-creates `data/logs/` if missing

### 🎮 Business Logic Layer

#### `handlers.py` - Monolithic Handler 🚨
```python
# Critical sections:
- process_user_input() [Line ~58]: Main text processing pipeline
- process_thread_with_photos() [Line ~107]: Photo + text processing  
- Message threading system [Lines 36-103]: Global state with locks
```

**Threading System Architecture**:
```python
message_threads = defaultdict(list)  # Global state 🚨
last_message_time = defaultdict(float)
_message_threads_lock = threading.Lock()
THREAD_TIMEOUT = 1.0  # Groups messages within 1 second
```

**Key Handler Patterns**:
- **Command Handlers**: `/start`, `/recipients`, `/settings`
- **Callback Handlers**: Button press processing with data parsing
- **State Handlers**: FSM-based flows for recipient setup
- **Message Handlers**: Text, voice, photo processing with threading

**Commented Code Block**: Lines 1746-1837 contain broken `handle_regular_message_BROKEN` function - **can be removed**.

### 🤖 AI & Processing Services

#### `services/parsing_service.py` - Hybrid Time Parser ⭐
```python
class ParsingService(IParsingService):
    def _calculate_precise_time()  # Rule-based calculation
    def parse_content_to_task()    # Hybrid LLM + rules
```

**Hybrid Architecture**:
1. **Precise Calculation**: Handles patterns LLMs struggle with
   - "today 5am" → Date math with timezone conversion
   - "in 2 hours" → Exact time addition
   - "Nov 25" → Date parsing with fallbacks

2. **LLM Processing**: OpenAI GPT-4.1-mini for complex language
   - Title/description generation
   - Complex temporal expressions
   - Context understanding

**Time Patterns Supported**:
```python
# Regex patterns in _calculate_precise_time():
today_pattern = r'\btoday\s+(?:at\s+)?(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm)?|noon|midnight)\b'
relative_pattern = r'(?:\bin\s+(\d+)\s+(minute|hour|day|week)...'
month_pattern = r'\b(jan|feb|mar|...)...'
```

**Coverage**: 72% test coverage, handles ALL user-reported edge cases.

#### `services/clean_recipient_service.py` - Recipient Management
```python
class CleanRecipientService:
    def get_default_recipients()   # Smart defaults with fallback
    def toggle_default_status()    # Default recipient management
    def get_enabled_recipients()   # Active recipient filtering
```
- **Pattern**: Repository pattern with business logic layer
- **Data Model**: Uses `UnifiedRecipient` model (current approach)
- **GDPR**: Implements `delete_user_data()` for compliance

#### `services/clean_recipient_task_service.py` - Task Orchestration
```python
class CleanRecipientTaskService:
    def create_task_for_recipients()  # Main orchestration method
```
**Flow**:
1. Resolve recipients (specific vs defaults)
2. Create local task record
3. Distribute to platform APIs via factory
4. Collect URLs and action suggestions
5. Format user feedback with action buttons

### 💾 Data Layer

#### `database/connection.py` - Database Management
```python
class DatabaseManager:
    def get_connection()     # Thread-safe connection pooling
    def execute_transaction() # Auto-rollback on error
```
- **Features**: WAL mode, foreign keys enabled, connection pooling
- **Thread Safety**: `threading.local()` for per-thread connections
- **Transactions**: Context managers with automatic rollback

#### `database/unified_recipient_repository.py` - Current Data Layer ✅
```python
class UnifiedRecipientRepository:
    # Clean implementation using unified recipient model
    # Replaces legacy split-table approach
```
- **Model**: Single `recipients` table with boolean flags for type
- **Relations**: Proper foreign keys to `user_preferences_unified`
- **Operations**: Full CRUD with business logic integration

#### `database/recipient_repositories.py` - Legacy System 🚨
```python
# Legacy split-table approach:
UserPlatformRepository + SharedRecipientRepository
```
- **Issue**: Parallel implementation of same functionality
- **Status**: Should be removed after data migration
- **Tables**: `user_platforms`, `shared_recipients`, `user_preferences_v2`

### 🔌 Platform Integration

#### `platforms/base.py` - Platform Abstraction
```python
@abstractmethod
def create_task(task_data: Dict[str, Any]) -> Optional[str]:
    # Returns task ID or None
    
@register_platform('platform_name')  # Auto-registration decorator
class ConcretePlatform(AbstractTaskPlatform):
```
- **Pattern**: Abstract Factory + Registry pattern
- **Factory**: `TaskPlatformFactory.create_platform(platform_type, config)`
- **Registration**: Decorator-based auto-registration at import time

#### `platforms/todoist.py` - Todoist Integration  
```python
class TodoistPlatform(AbstractTaskPlatform):
    # REST API v2, Bearer token auth
    # Supports: tasks, projects, file attachments
```
- **API**: `https://api.todoist.com/rest/v2/`
- **Auth**: `Authorization: Bearer <token>`
- **Features**: Due dates, descriptions, file attachments up to 25MB

#### `platforms/trello.py` - Trello Integration
```python  
class TrelloPlatform(AbstractTaskPlatform):
    # REST API v1, API key + token auth
    # Supports: cards, lists, file attachments
```
- **API**: `https://api.trello.com/1/`
- **Auth**: URL params `key=<key>&token=<token>`
- **Features**: Card creation, attachments up to 10MB

**Common Issues**:
- Code duplication in HTTP handling
- No retry logic for transient failures
- Generic exception handling

### 🎯 UI & State Management

#### `keyboards/recipient.py` - UI Components
```python
def get_recipient_management_keyboard() # Main recipient menu
def get_platform_selection_keyboard()  # Platform type selection
def get_post_task_actions_keyboard()   # Post-creation actions
```
- **Pattern**: Factory functions returning `InlineKeyboardMarkup`
- **Data Encoding**: Callback data with structured format `action:param:param`
- **Dynamic**: Keyboards adapt based on user state and data

#### `states/recipient_states.py` - FSM States
```python
class RecipientState(StatesGroup):
    waiting_for_recipient_name = State()
    waiting_for_todoist_token = State()
    waiting_for_trello_credentials = State()
```
- **Framework**: Aiogram FSM for state management
- **Scope**: Per-user state isolation
- **Persistence**: States persist across bot restarts

### ⏰ Scheduling System

#### `scheduler.py` - Task Scheduler
```python
async def task_scheduler(bot: Bot):
    while True:
        # Poll for due tasks every N seconds
        # Send notifications via Telegram
        await asyncio.sleep(interval)
```
- **Pattern**: Simple polling loop with error isolation
- **Frequency**: Configurable via `SCHEDULER_INTERVAL` (default: 60s)
- **Error Handling**: Individual task failures don't stop scheduler
- **Issue**: Global bot instance dependency

## 🧹 Code Quality Issues

### 🚨 Critical Issues

#### 1. Data Layer Fragmentation
```python
# THREE competing systems:
models/recipient.py          # Legacy split-table
models/unified_recipient.py  # Current unified approach  
database/recipient_repositories.py    # Legacy repos
database/unified_recipient_repository.py # Current repo
```

#### 2. Monolithic Handler
```python
# handlers.py (1,998 lines):
- 47 different handler functions
- Global threading state management
- Mixed UI, business logic, state management
- Code duplication between photo/text processing
```

#### 3. Container Architecture
```python
# Dual containers without clear separation:
ApplicationContainer     # Core services
RecipientContainer      # Recipient services
# Should be unified into logical provider groups
```

### ⚠️ Technical Debt

#### Commented Code Blocks
```python
# handlers.py lines 1746-1837: 
# Entire function commented out with "BROKEN - COMMENTED OUT"
# Safe to remove - replaced by threading version
```

#### Legacy Files
```
handlers.py.bak, handlers.py.bak2  # Backup files
test_*.py (root level)             # Development scripts  
validate_architecture.py          # Analysis scripts
```

## 🎯 Non-Obvious Implementation Details

### Message Threading Algorithm
```python
# groups messages within 1-second windows:
1. Add message to thread buffer
2. Set timeout for THREAD_TIMEOUT seconds  
3. If no new messages arrive, process entire thread
4. Use locks to prevent race conditions
```

### Timezone Handling Strategy
```python
# Three-layer approach:
1. Rule-based patterns (precise calculation)
2. LLM parsing (complex language) 
3. Timezone conversion (zoneinfo + DST)
```

### Platform Factory Registration
```python
# Auto-registration at import time:
@register_platform('todoist')
class TodoistPlatform:
    # Automatically available in factory
```

### Database Transaction Pattern
```python
# All repositories use:
with self.db_manager.get_connection() as conn:
    with conn.transaction():
        # Operations auto-rollback on exception
```

This reference should provide quick orientation for development sessions and architectural decision making.