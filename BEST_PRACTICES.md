# Best Practices & Development Guidelines

## 🚨 **MANDATORY ANALYSIS BEFORE ANY TASK**

### **🔍 PRE-IMPLEMENTATION ANALYSIS PROTOCOL**

**BEFORE starting ANY coding task, you MUST:**

1. **📖 READ EXISTING TEST FILES**
   - Understand current testing patterns (`conftest.py`, existing unit tests)
   - Check for Factory Boy usage: `grep -r "import factory" tests/`
   - Identify established fixture patterns
   - Never revert from Factory Boy to mocks

2. **📋 READ EXISTING MD FILES**  
   - Check for existing planning documents in project root
   - Follow established approaches (e.g., `PLANNING_TEMPLATE.md`)
   - Look for architectural decisions and patterns
   - Avoid duplicating or contradicting existing plans

3. **🏗️ VERIFY ARCHITECTURE DECISIONS**
   - Check dependency injection patterns (`core/container.py`)
   - Verify service layer patterns (`services/`)
   - Understand repository patterns (`database/`)
   - Follow established error handling patterns

4. **📝 DOCUMENT ANALYSIS FINDINGS**
   - Record what tools/patterns are already in use
   - Note any architectural improvements already implemented
   - Identify gaps that need addressing
   - Document reasons for chosen approach

### **⛔ NEVER REVERT ARCHITECTURAL IMPROVEMENTS**
- ❌ Don't replace Factory Boy with mocks
- ❌ Don't bypass dependency injection
- ❌ Don't ignore established service patterns  
- ❌ Don't duplicate existing planning documents

---

## 🚨 **CRITICAL DEPLOYMENT REQUIREMENTS**

### **Container Conflicts - NEVER RUN DUPLICATE BOT INSTANCES**

⚠️ **MANDATORY CHECK BEFORE DEPLOYMENT**: Always verify no duplicate bot containers are running with the same token.

**Problem**: Running multiple containers with the same Telegram bot token causes:
- `TelegramConflictError: terminated by other getUpdates request`
- Bot cannot receive messages
- Silent failures in message processing

**Solution**:
1. **ALWAYS** run `docker ps` before starting bot
2. **ALWAYS** stop any conflicting containers: `docker stop infra-bot-1` 
3. **NEVER** run multiple containers with same bot token simultaneously
4. Use unique tokens for development vs production environments

**Example of proper cleanup**:
```bash
# Check for conflicts
docker ps | grep bot

# Stop conflicting containers  
docker stop infra-bot-1 tghandyutils-bot-1

# Start only one instance
docker-compose up -d
```

## 🏛️ **Architectural Principles**

### 1. **Single Responsibility Principle**
- Each class/module has ONE clear purpose
- Services handle business logic only
- Repositories handle data access only
- Models represent data structures only

**Example**:
```python
# ✅ Good: Single responsibility
class RecipientService:
    def get_all_recipients(self, user_id: int) -> List[Recipient]:
        # Business logic for combining platforms and shared recipients

class UserPlatformRepository:
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
        # Data access only
```

### 2. **Dependency Injection**
- Always use DI container for service dependencies
- Never create services directly in handlers
- Use interfaces for loose coupling

**Example**:
```python
# ✅ Good: Dependency injection
@inject
def get_recipient_service(
    service: IRecipientService = Provide[RecipientContainer.recipient_service]
) -> IRecipientService:
    return service

# ❌ Bad: Direct instantiation
def some_handler():
    service = RecipientService(repo1, repo2, repo3)  # DON'T
```

### 3. **Repository Pattern**
- Repositories abstract data access
- One repository per aggregate root
- Use interfaces for testability

**Pattern**:
```python
class IUserPlatformRepository(ABC):
    @abstractmethod
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
        pass

class UserPlatformRepository(IUserPlatformRepository):
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
        # Implementation
```

## 🎨 **Code Quality Standards**

### 1. **Type Hints**
- Use type hints for all function parameters and return types
- Import types from `typing` module
- Use `Optional` for nullable values

**Example**:
```python
# ✅ Good: Proper type hints
def create_task(
    self, 
    user_id: int, 
    task_data: TaskCreate,
    recipient_ids: Optional[List[str]] = None
) -> Tuple[bool, Optional[str]]:
    pass
```

### 2. **Error Handling**
- Use specific exception types
- Log errors with context
- Handle exceptions at appropriate levels

**Example**:
```python
# ✅ Good: Specific exceptions with logging
try:
    result = platform.create_task(task_data)
    if not result:
        raise TaskCreationError(f"Platform {platform_type} returned no task ID")
except PlatformError as e:
    logger.error(f"Platform error for user {user_id}: {e}")
    raise TaskCreationError(f"Platform failure: {e}")
```

### 3. **Logging**
- Use structured logging with context
- Log at appropriate levels (DEBUG, INFO, WARNING, ERROR)
- Never log sensitive data (credentials, tokens)

**Example**:
```python
# ✅ Good: Structured logging
logger.info(f"Creating task for user {user_id} on {platform_type}")
logger.debug(f"Task data: title='{task_data.title}', due='{task_data.due_time}'")
logger.error(f"Failed to create task: {e}", exc_info=True)

# ❌ Bad: Logging credentials
logger.info(f"Using token: {credentials}")  # DON'T
```

## 🗄️ **Database Best Practices**

### 1. **Schema Design**
- Use `telegram_user_id` directly (no internal user IDs)
- Enable foreign keys for data integrity
- Create proper indexes for performance

**Example**:
```sql
-- ✅ Good: Clean schema
CREATE TABLE user_platforms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL,
    platform_type TEXT NOT NULL,
    credentials TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    UNIQUE(telegram_user_id, platform_type)
);

CREATE INDEX idx_user_platforms_user_id ON user_platforms(telegram_user_id);
```

### 2. **Connection Management**
- Use context managers for database connections
- Enable WAL mode for better concurrency
- Handle connection timeouts properly

**Example**:
```python
# ✅ Good: Context manager
def get_user_platforms(self, user_id: int) -> List[UserPlatform]:
    with self.db_manager.get_connection() as conn:
        cursor = conn.execute(query, (user_id,))
        return [self._row_to_model(row) for row in cursor.fetchall()]
```

### 3. **Query Optimization**
- Use parameterized queries (prevents SQL injection)
- Limit result sets when appropriate
- Use proper indexes

## 🤖 **Telegram Bot Best Practices**

### 1. **Handler Organization**
- One handler per user action
- Use FSM states for multi-step flows
- Clear state properly

**Example**:
```python
# ✅ Good: Clear handler responsibility
@router.callback_query(lambda c: c.data.startswith("select_recipient_"))
async def handle_recipient_selection(callback_query: CallbackQuery, state: FSMContext):
    """Handle recipient selection for task creation."""
    # Implementation
    await state.update_data(selected_recipients=selected_recipients)
```

### 2. **Keyboard Design**
- Use descriptive button text
- Show current state (selected/not selected)
- Provide clear navigation paths

**Example**:
```python
# ✅ Good: Clear visual feedback
def get_recipient_selection_keyboard(recipients: List[Recipient], selected: List[str]) -> InlineKeyboardMarkup:
    buttons = []
    for recipient in recipients:
        status = "☑️" if recipient.id in selected else "☐"
        buttons.append(InlineKeyboardButton(
            text=f"{status} {recipient.name}",
            callback_data=f"select_recipient_{recipient.id}"
        ))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])
```

### 3. **State Management**
- Use FSM for multi-step workflows
- Clear state when operations complete
- Handle state cleanup on errors

**Example**:
```python
# ✅ Good: Proper state management
@router.message(RecipientState.waiting_for_credentials)
async def handle_credentials(message: Message, state: FSMContext):
    try:
        # Process credentials
        await message.reply("✅ Success!")
        await state.clear()  # Always clear state
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.reply("❌ Error occurred")
        await state.clear()  # Clear even on error
```

## 🧪 **Testing Standards** ✅

### 1. **Achievement Status**
- ✅ **149 Tests Passing** with 100% pass rate
- ✅ **49% Code Coverage** across all core components  
- ✅ **Comprehensive Test Suite** covering models, services, repositories, platforms, UI
- ✅ **Docker Integration** for isolated test environment
- ✅ **Unified Test Runner** with production-like environment

### 2. **Unit Test Structure**
- Test one component at a time ✅
- Mock all dependencies ✅  
- Use descriptive test names ✅

### 3. **Test Execution Tools**
```bash
# Unified test runner (recommended)
./run-tests.sh unit                    # Fast unit tests (149 tests)
./run-tests.sh integration             # Integration tests with real APIs
./run-tests.sh all                     # Complete test suite
./run-tests.sh unit test_models.py     # Specific test files
./run-tests.sh unit "test_models.py::TestTask" # Specific test classes

# Legacy runners (deprecated)
./test-dev.sh unit                     # Legacy unit test runner
./test.sh                              # Legacy comprehensive runner
```

**Example**:
```python
class TestRecipientService:
    def test_get_all_recipients_combines_platforms_and_shared(self, recipient_service):
        """Test that all recipients includes both user platforms and shared recipients."""
        recipients = recipient_service.get_all_recipients(12345)
        
        assert len(recipients) == 2
        assert any(r.type == "user_platform" for r in recipients)
        assert any(r.type == "shared_recipient" for r in recipients)
```

### 2. **Test Coverage**
- Test happy path scenarios
- Test error conditions
- Test edge cases (empty lists, null values)

### 3. **Fixtures and Mocks**
- Use consistent test data
- Mock external dependencies
- Keep tests isolated

### 4. **Test Naming Standards**
- **✅ Good**: `test_scheduling_integration_future_dates()` - Describes functionality being tested
- **✅ Good**: `test_timezone_conversion_accuracy()` - Clear, professional test purpose
- **❌ Bad**: `test_timezone_bug_reproduction()` - References specific bugs, temporary
- **❌ Bad**: `test_fix_for_issue_123()` - Implementation-focused, not behavior-focused

**Principle**: Test names should describe the expected behavior or functionality, not bugs or fixes. Tests should be permanent validation of system behavior, not temporary bug reproductions.

### 5. **Integration Test Standards**
- **Environment Setup**: Always use proper docker-compose files, not one-off scripts
- **Configuration**: Load real API keys from `.env` file for integration tests
- **Test Runners**: Create universal, parameterized test runners (not single-purpose scripts)
- **Documentation**: Document test infrastructure in appropriate .md files
- **Isolation**: Run tests in isolated containers, not in production environment

**Example**:
```bash
# ✅ Good: Universal test runner
./test-integration.sh test_scheduling.py -k "midnight"

# ❌ Bad: One-off test script
./run-single-specific-test-for-bug-123.sh
```

### 6. **Critical Integration Test Lesson: Mock vs Real Implementation**
- **❌ DANGER**: Mocking critical business logic can hide implementation bugs
- **✅ SOLUTION**: Use real implementations for end-to-end integration tests
- **Real Case**: Screenshot attachment bug was hidden by mocking `create_task_for_recipients()`

**What went wrong:**
```python
# ❌ BAD: Mock hides missing attach_screenshot() call
@patch('handlers.container.clean_recipient_task_service')
def test_screenshot_processing(mock_task_service):
    mock_task_service.return_value.create_task_for_recipients.return_value = (True, "Success", {})
    # This never tests if attach_screenshot() is actually called!
```

**Better approach:**
```python
# ✅ GOOD: Test with real service + mock platform calls
def test_screenshot_attachment_real_service():
    real_service = CleanRecipientTaskService(...)
    with patch('platforms.todoist.TodoistPlatform.attach_screenshot') as mock_attach:
        result = real_service.create_task_for_recipients(..., screenshot_data=data)
        mock_attach.assert_called_once_with(task_id, image_bytes, filename)
```

**Rule**: Mock external dependencies (APIs, databases) but use real business logic implementations.

### 7. **Test Suite Unification Lessons**
- **❌ PROBLEM**: Multiple scattered test runners (`test-dev.sh`, `test-integration.sh`, `test.sh`)
- **❌ PROBLEM**: Different Docker environments for unit vs integration tests  
- **❌ PROBLEM**: No unified parameterization (can't easily run specific tests across types)
- **✅ SOLUTION**: Single unified test runner with consistent environment

**Before (fragmented):**
```bash
# ❌ BAD: Multiple different runners
./test-dev.sh unit                    # Different environment
./test-integration.sh test_file.py    # Different docker compose
./test.sh                            # Yet another approach
```

**After (unified):**
```bash
# ✅ GOOD: Single unified runner
./run-tests.sh unit                           # Unit tests in production-like env
./run-tests.sh integration test_screenshot.py # Integration tests, same env
./run-tests.sh all                           # Both types with unified reporting
./run-tests.sh unit "test_models.py::TestTask" # Specific test selection
```

**Key Principles:**
1. **Same Environment**: Both unit and integration tests use same Docker setup (production-like)
2. **Environment Variables**: Mock tokens for unit tests, real APIs for integration tests
3. **Unified Parameterization**: Single interface for test selection and configuration
4. **Consistent Reporting**: Same coverage and output format for all test types
5. **Clear Documentation**: Help system explains usage and environment requirements

**Environment Strategy:**
- **Unit Tests**: `OPENAI_API_KEY=test_key_not_used` (no API costs)
- **Integration Tests**: Load real `.env` file (requires valid API keys)
- **Same Docker Image**: Ensures consistency between test environments and production

## 🚀 **Performance Guidelines**

### 1. **Database Performance**
- Use connection pooling
- Create appropriate indexes
- Avoid N+1 query problems

### 2. **Memory Management**
- Don't store large objects in memory
- Clean up resources properly
- Use generators for large datasets

### 3. **Async/Await**
- Use async for I/O operations
- Don't block the event loop
- Handle async exceptions properly

## 🔒 **Security Best Practices**

### 1. **Credential Handling**
- Never log credentials or tokens
- Store credentials securely
- Use environment variables for secrets

### 2. **Input Validation**
- Validate all user inputs
- Use parameterized queries
- Sanitize data before storage

### 3. **Error Messages**
- Don't expose internal details in error messages
- Log detailed errors internally
- Show user-friendly messages to users

## 📁 **File Organization**

### 1. **Directory Structure**
```
project/
├── core/                 # Core application logic
│   ├── interfaces.py     # Abstract interfaces
│   ├── container.py      # DI container
│   └── exceptions.py     # Custom exceptions
├── database/             # Data access layer
│   ├── repositories.py   # Repository implementations
│   └── connection.py     # Database management
├── services/             # Business logic layer
├── models/               # Data models
├── handlers.py           # Telegram handlers
├── keyboards/            # Keyboard definitions
└── tests/                # Test files
```

### 2. **Import Organization**
```python
# Standard library imports
import json
from typing import List, Optional

# Third-party imports
from aiogram import Router
from aiogram.types import Message

# Local imports
from core.interfaces import IRecipientService
from models.recipient import Recipient
```

## ✅ **Code Review Checklist**

### Before Committing:
- [ ] All functions have type hints
- [ ] Error handling is present and appropriate
- [ ] Logging is informative but doesn't expose secrets
- [ ] Tests are written and passing
- [ ] No hardcoded values
- [ ] No global state mutations
- [ ] Proper separation of concerns
- [ ] Dependencies are injected, not instantiated
- [ ] Database connections use context managers
- [ ] State is cleaned up properly

### Architecture Compliance:
- [ ] Single responsibility principle followed
- [ ] Repository pattern used for data access
- [ ] Service layer handles business logic
- [ ] No legacy code references
- [ ] Clean, understandable interfaces
- [ ] Proper error propagation

## 🎯 **Development Workflow**

### 1. **Feature Development**
1. Write interface/contract first
2. Implement repository/data layer
3. Implement service/business layer
4. Write handlers/UI layer
5. Write comprehensive tests
6. Update documentation

### 2. **Bug Fixes - TEST-FIRST MANDATORY**
**CRITICAL**: NEVER fix a bug without first writing a failing test that demonstrates it.

**⚠️ ABSOLUTELY FORBIDDEN SEQUENCE ⚠️**:
❌ Find bug in logs → Fix code → Deploy fix

**✅ MANDATORY SEQUENCE FOR ALL BUGS ✅**:
1. **User Reports Bug** → "manage accounts click fails"
2. **Write Failing Test** → Create test that reproduces the exact failure
3. **Run Test & Verify It Fails** → Confirm test catches the bug
4. **Fix the Bug** → Make minimal changes to pass the test
5. **Run Test & Verify It Passes** → Confirm fix works
6. **Run Full Test Suite** → Ensure no regressions
7. **Deploy Fix** → Ship the tested solution

**🔥 IDIOT-PROOF CHECKLIST 🔥**:
- [ ] I have a failing test that reproduces the bug
- [ ] I ran the test and it failed (red)
- [ ] I fixed the code 
- [ ] I ran the test and it passed (green)
- [ ] I ran the full test suite
- [ ] ONLY NOW can I deploy/commit

**Test Selection by Bug Type**:
- **Missing Functions**: Unit test that imports and calls the function
- **Callback Patterns**: Integration test that simulates button press
- **Database Schema**: Repository test that exercises all expected fields
- **Navigation Issues**: UI test that verifies all screens have exit paths
- **Logic Errors**: Unit test with specific input/output assertions

**Real Example - "manage accounts click fails"**:
```python
# ✅ CORRECT: Write test FIRST
def test_manage_accounts_callback_works():
    """Test that manage accounts callback doesn't crash."""
    service = RecipientService(mock_repository)
    # This should fail initially due to missing method
    recipients = service.get_recipients_by_user(12345)
    assert isinstance(recipients, list)

# ❌ WRONG: Fix code first without test
# Just adding get_recipients_by_user() method directly
```

**Why This Protocol is NON-NEGOTIABLE**:
1. **Regression Prevention**: Test ensures bug never returns
2. **Confidence**: Proves fix actually works 
3. **Documentation**: Test explains what was broken
4. **Quality**: Forces thinking about edge cases
5. **Safety**: Prevents breaking other functionality

### **Test Infrastructure Maintenance**
**CRITICAL**: Distinguish between functionality bugs vs test infrastructure issues

**When Tests Fail**:
1. **First Check**: Is functionality actually broken or just test mocking?
2. **Verify Manually**: Test actual functionality in running application
3. **Check Integration Tests**: Often more reliable than unit tests with complex mocks
4. **Fix vs Skip**: Fix test infrastructure separately from functional bugs

**Mock Debugging Principles**:
- **Import Location Matters**: Patch where function is used, not where defined
- **Function-Level Imports**: For imports inside functions, patch the using module
- **Test Isolation**: Failing mocks ≠ Missing functionality
- **Integration Over Unit**: Use integration tests for complex interaction verification

### 3. **Refactoring**
1. Ensure comprehensive test coverage
2. Make incremental changes
3. Run tests after each change
4. Update documentation if needed

---

## 📋 **Summary**

These best practices ensure:
- **Maintainable**: Easy to understand and modify
- **Testable**: Comprehensive test coverage
- **Scalable**: Can handle growth and new features
- **Reliable**: Robust error handling and validation
- **Secure**: Proper credential and data handling
- **Professional**: Industry-standard patterns and practices

Following these guidelines will keep the codebase clean, professional, and bug-free.