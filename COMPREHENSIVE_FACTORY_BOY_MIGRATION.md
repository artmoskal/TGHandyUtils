# COMPREHENSIVE FACTORY BOY MIGRATION - IN PROGRESS ⚠️

> **⚠️ FACTORY BOY MIGRATION INCOMPLETE** ⚠️  
> **❌ 30 TESTS FAILING + 18 ERRORS** - Major work still needed  
> **❌ MOCK CONTAMINATION STILL EXISTS** - Need to complete cleanup  
> **✅ FACTORY INFRASTRUCTURE CREATED** - Foundation is solid

## ❌ **MIGRATION STATUS - WORK REQUIRED**

### **Current Test Results (FAILING):**
- **183 tests collected** - imports working ✅  
- **135 tests passing** - only 74% pass rate ❌
- **30 tests failing** - significant failures ❌
- **18 errors** - major configuration issues ❌
- **7 files still using unittest.mock** - cleanup incomplete ❌
- **Comprehensive factory infrastructure** created ✅

### **Key Technical Achievements:**
- **SimpleObject pattern** implemented for attribute-based access
- **Realistic test data generation** with Faker integration  
- **Factory hierarchy** supporting complex scenarios and edge cases
- **Test quality maintained** with enhanced realistic scenarios

---

## 📋 COMPREHENSIVE MIGRATION PLAN

### 🚀 CURRENT STATUS
**Currently Working On:** Factory Boy Migration + Shared Account Investigation  
**Last Updated:** 2025-07-04  
**Critical Issues:** 2 major architectural problems

**✅ COMPLETED:**
- ✅ Comprehensive codebase analysis 
- ✅ Test count analysis (34 files, 242 methods, 79% mock contamination)
- ✅ Shared account bug confirmation

**🚧 IN PROGRESS:**
- Creating comprehensive migration plan

**📋 PENDING:**
- Migrate all 27 mock-based test files to Factory Boy
- Fix shared account creation workflow
- Investigate why shared accounts become personal

---

## 🔥 SECTION 1: CRITICAL - SHARED ACCOUNT INVESTIGATION

### Overview
**URGENT:** Shared account creation is broken - `add_shared_recipient` creates personal accounts instead of shared ones.

### 📊 Investigation Commands
```bash
# Check current shared account creation workflow
echo "=== Shared Account Creation Flow ==="
grep -n "add_shared_recipient" services/recipient_service.py
grep -n "is_personal=False" services/recipient_service.py

echo "=== State Handler Flow ==="  
grep -n "shared_recipient" handlers_modular/states/recipient_setup.py
grep -n "mode.*shared" handlers_modular/states/recipient_setup.py

echo "=== Database Evidence ==="
docker exec bot 'sqlite3 data/db/tasks.db "SELECT id, name, is_personal, created_at FROM recipients WHERE user_id = 447812312 ORDER BY id"'
```

### Root Cause Analysis
```
# PROBLEM: add_shared_recipient creates personal accounts (is_personal=True)
# EVIDENCE: Original "Al" (ID 2) deleted, new "al" (ID 4) created as personal  
# IMPACT: Shared accounts treated as personal, wrong task creation behavior
# SUSPECTS: 
#   1. add_shared_recipient service method
#   2. State handler mode setting
#   3. Repository recipient creation
```

### Detailed Investigation Checklist

#### **1.1 Service Layer Analysis (High Priority)**
- [ ] **Trace `add_shared_recipient` method implementation**
  ```python
  # Expected: is_personal=False parameter passing
  def add_shared_recipient(self, user_id: int, name: str, platform_type: str, credentials: str) -> bool:
      return self.repository.add_recipient(
          user_id=user_id, 
          name=name, 
          platform_type=platform_type, 
          credentials=credentials,
          is_personal=False  # ← CRITICAL: This must be False
      )
  ```
- [ ] **Verify method signature matches usage in state handlers**
- [ ] **Check repository method call parameters** 
- [ ] **Test service method in isolation with mock repository**
- [ ] **Verify no parameter override or default value issues**
- [ ] **Check for typos in parameter names (is_personal vs ispersonal)**
- [ ] **Verify method exists and is not calling wrong repository method**
- [ ] **Check if method accidentally calls personal recipient creation**
- [ ] **Validate service container DI configuration**
- [ ] **Test method with real database to isolate service vs repository issues**

#### **1.2 State Handler Flow Analysis (High Priority)**
- [ ] **Check `RecipientState.waiting_for_recipient_name` handler**
  ```python
  # Expected flow: shared_recipient mode → add_shared_recipient call
  @router.message(RecipientState.waiting_for_recipient_name)
  async def handle_recipient_name_input(message: Message, state: FSMContext):
      state_data = await state.get_data()
      mode = state_data.get("mode")  # Should be "shared_recipient"
      if mode == "shared_recipient":
          # Should call add_shared_recipient, not add_personal_recipient
          success = recipient_service.add_shared_recipient(...)
  ```
- [ ] **Verify `mode="shared_recipient"` state data preservation**
- [ ] **Trace from "Add Shared Account" button to final creation**
- [ ] **Check for state data corruption between steps**
- [ ] **Verify no conditional logic defaulting to personal creation**
- [ ] **Check if mode comparison is case-sensitive or has typos**
- [ ] **Validate state data doesn't get overwritten by other handlers**
- [ ] **Test state preservation across message handling**
- [ ] **Check if FSM context is properly maintained**
- [ ] **Verify no race conditions in state handling**

#### **1.3 Repository Layer Analysis (Critical)**
- [ ] **Check `UnifiedRecipientRepository.add_recipient` implementation**
  ```sql
  -- Expected SQL: is_personal parameter respected
  INSERT INTO recipients (user_id, name, platform_type, credentials, is_personal, enabled)
  VALUES (?, ?, ?, ?, ?, ?)
  -- Parameter 5 should be False for shared recipients
  ```
- [ ] **Verify `is_personal` field handling in SQL insert**
- [ ] **Check for parameter binding issues (wrong position)**
- [ ] **Verify no default value overrides in table schema**
- [ ] **Check recipient object construction from database rows**
- [ ] **Validate no type conversion issues (bool to int)**
- [ ] **Test repository method in isolation**
- [ ] **Check for SQL injection protection (parameterized queries)**
- [ ] **Verify database column mapping**
- [ ] **Test with various boolean representations (0/1, True/False)**

#### **1.4 Database Schema Analysis (Foundational)**
- [ ] **Check table schema for `is_personal` column definition**
  ```sql
  PRAGMA table_info(recipients);
  -- Should show: is_personal BOOLEAN or INTEGER with appropriate constraints
  ```
- [ ] **Verify no DEFAULT TRUE on is_personal column**
- [ ] **Check for database triggers affecting recipient creation**
- [ ] **Verify no CHECK constraints forcing is_personal=TRUE**
- [ ] **Check for data corruption during container restarts**
- [ ] **Trace recipient creation through SQLite database logs**
- [ ] **Verify foreign key constraints**
- [ ] **Check for index issues affecting writes**
- [ ] **Test database directly with raw SQL**
- [ ] **Validate database file permissions and integrity**

#### **1.5 Integration Flow Analysis (End-to-End)**
- [ ] **Test complete flow: Button Click → Database Record**
  ```python
  # Complete flow test
  # 1. Simulate "Add Shared Account" button click
  # 2. Enter recipient name
  # 3. Enter platform credentials  
  # 4. Verify database record has is_personal=False
  ```
- [ ] **Verify button callback data is correct**
- [ ] **Check state machine transitions**
- [ ] **Test with different platform types**
- [ ] **Verify error handling doesn't default to personal**
- [ ] **Check transaction rollback behavior**
- [ ] **Test with database connection issues**
- [ ] **Verify concurrent user handling**
- [ ] **Test container restart during creation**
- [ ] **Check memory state vs database state consistency**

### Success Criteria
- [ ] `add_shared_recipient` creates recipients with `is_personal=False`
- [ ] Shared recipients excluded from default task creation
- [ ] Shared recipients show confirmation buttons
- [ ] Database integrity maintained across container restarts

---

## 🔥 SECTION 2: CRITICAL - FACTORY BOY MASS MIGRATION

### Overview  
Migrate **ALL 27 mock-based test files** to Factory Boy in one comprehensive operation.

### 📊 Test File Analysis
```bash
# Files requiring migration (27 total):
echo "=== Mock-based test files to migrate ==="
find tests/ -name "test_*.py" | xargs grep -l "unittest.mock"

echo "=== Current Factory Boy usage ==="
find tests/ -name "test_*.py" | xargs grep -l "factory" || echo "NONE FOUND"

echo "=== Test method distribution ==="
find tests/ -name "test_*.py" | xargs grep -c "def test_" | sort -t: -k2 -nr
```

### Migration Strategy
**PHASE 1: Infrastructure (Foundation)**
- Create Factory Boy infrastructure for all domain models
- Setup test database with transaction isolation
- Create base factory classes and sequences

**PHASE 2: Core Domain Migration (High Impact)** 
- Migrate recipient/task/preference factories first
- Replace critical service and repository tests
- Focus on integration test migrations

**PHASE 3: Complete Migration (Comprehensive)**
- Migrate all remaining mock-based tests  
- Remove all `unittest.mock` imports
- Ensure 100% Factory Boy usage

### Detailed Implementation Checklist

#### **2.1 Factory Infrastructure Creation (Foundation)**

##### **Factory Directory Structure Setup**
- [ ] **Create comprehensive factory structure**
  ```
  tests/
  ├── factories/
  │   ├── __init__.py                # Factory exports and configuration
  │   ├── base.py                    # Base factory with database integration
  │   ├── recipient_factory.py       # UnifiedRecipient + SharedRecipient factories
  │   ├── task_factory.py           # Task creation factories
  │   ├── preferences_factory.py    # UserPreferences factories
  │   ├── platform_factory.py       # Platform configuration factories
  │   ├── message_factory.py        # Telegram message/callback factories
  │   ├── state_factory.py          # FSM state data factories
  │   └── integration_factory.py    # Complex integration scenario factories
  ```
- [ ] **Verify factory imports work across test modules**
- [ ] **Create factory registry for easy access**
- [ ] **Setup factory inheritance hierarchy**
- [ ] **Add factory validation for required fields**
- [ ] **Create factory documentation and examples**
- [ ] **Setup factory linting and type checking**
- [ ] **Test factory import performance**
- [ ] **Create factory debugging utilities**
- [ ] **Setup factory versioning and migration support**

##### **Database Integration Setup**  
- [ ] **Setup test database with proper transaction isolation**
  ```python
  # Base factory with database integration
  class BaseFactory(factory.Factory):
      class Meta:
          abstract = True
          strategy = factory.BUILD_STRATEGY  # Don't auto-save to DB
      
      @classmethod
      def _setup_database(cls):
          # Setup test database connection
          # Enable foreign keys, WAL mode, etc.
  ```
- [ ] **Implement factory database sequences for unique IDs**
- [ ] **Add automatic rollback mechanisms for test isolation**
- [ ] **Configure factory database session management**
- [ ] **Setup database fixture cleanup between tests**
- [ ] **Create database state verification utilities**
- [ ] **Test database connection pooling with factories**
- [ ] **Setup database migration testing with factories**
- [ ] **Add database performance monitoring for factory usage**
- [ ] **Create database backup/restore for factory testing**

##### **Base Factory Configuration**
- [ ] **Create comprehensive `BaseFactory` with common settings**
  ```python
  class BaseFactory(factory.Factory):
      class Meta:
          abstract = True
      
      # Common faker configuration
      fake = factory.Faker._get_faker(locale='en_US')
      
      # Sequence generators
      id = factory.Sequence(lambda n: n + 1000)  # Avoid conflicts with real data
      created_at = factory.LazyFunction(lambda: datetime.utcnow())
      updated_at = factory.LazyFunction(lambda: datetime.utcnow())
  ```
- [ ] **Setup sequence generators for all ID fields** 
- [ ] **Configure faker locales and custom providers**
- [ ] **Add custom factory traits for common test scenarios**
- [ ] **Create factory mixins for common patterns**
- [ ] **Setup factory callbacks for post-generation logic**
- [ ] **Add factory parameter validation**
- [ ] **Create factory debugging and introspection tools**
- [ ] **Setup factory performance optimization**
- [ ] **Add factory thread safety configuration**

#### **2.2 Domain Model Factories (Priority Order)**

##### **2.2.1 UnifiedRecipient Factory (Highest Priority)**
- [ ] **Create comprehensive recipient factory with all variants**
  ```python
  class UnifiedRecipientFactory(factory.Factory):
      class Meta:
          model = UnifiedRecipient
      
      id = factory.Sequence(lambda n: n + 1000)  # Avoid ID conflicts
      user_id = factory.Faker('random_int', min=100000, max=999999)
      name = factory.Faker('company')
      platform_type = factory.Iterator(['todoist', 'trello', 'notion'])
      credentials = factory.Faker('password', length=32)
      is_personal = True  # Default personal
      enabled = True
      created_at = factory.LazyFunction(datetime.utcnow)
      updated_at = factory.LazyFunction(datetime.utcnow)
  
  class SharedRecipientFactory(UnifiedRecipientFactory):
      """Factory for shared recipients - CRITICAL for testing shared account bug"""
      is_personal = False  # ← This is the critical field causing the bug
      name = factory.LazyAttribute(lambda obj: f"{obj.name} (Shared)")
  
  class TodoistRecipientFactory(UnifiedRecipientFactory):
      platform_type = 'todoist'
      credentials = factory.Faker('password', length=40)  # Todoist token length
  
  class TrelloRecipientFactory(UnifiedRecipientFactory):
      platform_type = 'trello'
      credentials = factory.Faker('uuid4')  # Trello API key format
  ```
- [ ] **Test factory creates objects with correct is_personal values**
- [ ] **Verify factory supports all platform types**
- [ ] **Test factory with database persistence**
- [ ] **Create factory traits for enabled/disabled states**
- [ ] **Add factory validation for credential formats**
- [ ] **Test factory sequence generation for unique IDs**
- [ ] **Create factory subfactories for each platform**
- [ ] **Test factory with real database constraints**
- [ ] **Add factory debugging for is_personal field issues**

##### **2.2.2 Task Factory (High Priority)**
- [ ] **Create comprehensive task creation factory**
  ```python
  class TaskFactory(factory.Factory):
      class Meta:
          model = TaskCreate
      
      title = factory.Faker('sentence', nb_words=4)
      description = factory.Faker('text', max_nb_chars=200)
      due_time = factory.LazyFunction(
          lambda: (datetime.now() + timedelta(days=randint(1, 30))).isoformat()
      )
      priority = factory.Iterator(['low', 'medium', 'high', 'urgent'])
      labels = factory.LazyFunction(lambda: [faker.word() for _ in range(randint(0, 3))])
      
  class UrgentTaskFactory(TaskFactory):
      priority = 'urgent'
      due_time = factory.LazyFunction(
          lambda: (datetime.now() + timedelta(hours=randint(1, 24))).isoformat()
      )
  
  class LongTermTaskFactory(TaskFactory):
      due_time = factory.LazyFunction(
          lambda: (datetime.now() + timedelta(days=randint(30, 365))).isoformat()
      )
  ```
- [ ] **Test factory creates valid task objects**
- [ ] **Verify factory generates realistic due dates**
- [ ] **Test factory with various priority levels**
- [ ] **Create factory for tasks with attachments**
- [ ] **Test factory integration with recipient factories**
- [ ] **Add factory validation for required fields**
- [ ] **Test factory with screenshot attachment data**
- [ ] **Create factory for recurring tasks**
- [ ] **Test factory with platform-specific requirements**

##### **2.2.3 UserPreferences Factory (Medium Priority)**
- [ ] **Create user preferences factory**
  ```python
  class UserPreferencesFactory(factory.Factory):
      class Meta:
          model = UserPreferences
      
      user_id = factory.Faker('random_int', min=100000, max=999999)
      timezone = factory.Faker('timezone')
      default_due_time = factory.Faker('time', pattern='%H:%M')
      reminder_enabled = factory.Faker('boolean', chance_of_getting_true=80)
      reminder_minutes = factory.Iterator([0, 15, 30, 60, 120])
      language = factory.Iterator(['en', 'ru', 'es', 'fr'])
      date_format = factory.Iterator(['DD.MM.YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD'])
  ```
- [ ] **Test factory creates valid preference objects**
- [ ] **Verify factory generates valid timezones**
- [ ] **Test factory with various language settings**
- [ ] **Create factory for disabled preferences**
- [ ] **Test factory integration with user factories**
- [ ] **Add factory validation for time formats**
- [ ] **Test factory with edge case values**
- [ ] **Create factory for migration scenarios**
- [ ] **Test factory with database constraints**

##### **2.2.4 Platform Configuration Factory (Medium Priority)**
- [ ] **Create platform-specific configuration factories**
  ```python
  class PlatformConfigFactory(factory.Factory):
      class Meta:
          model = dict  # Platform configs are typically dicts
      
      @factory.lazy_attribute
      def todoist_config(self):
          return {
              'api_token': faker.password(length=40),
              'project_id': faker.random_int(min=1, max=999999),
              'sync_enabled': True,
              'webhook_url': faker.url()
          }
  
  class TodoistConfigFactory(PlatformConfigFactory):
      platform_type = 'todoist'
      api_version = 'v2'
      rate_limit = 450  # Todoist API limit
  
  class TrelloConfigFactory(PlatformConfigFactory):
      platform_type = 'trello'
      api_key = factory.Faker('uuid4')
      board_id = factory.Faker('uuid4')
  ```
- [ ] **Test factory creates platform-specific configs**
- [ ] **Verify factory handles API version differences**
- [ ] **Test factory with authentication scenarios**
- [ ] **Create factory for invalid/expired configs**
- [ ] **Test factory integration with recipient factories**
- [ ] **Add factory validation for API formats**
- [ ] **Test factory with real API constraints**
- [ ] **Create factory for config migration scenarios**
- [ ] **Test factory with error handling**

##### **2.2.5 Telegram Message Factory (Integration)**
- [ ] **Create Telegram message/callback factories**
  ```python
  class TelegramMessageFactory(factory.Factory):
      class Meta:
          model = dict  # Telegram messages are dicts
      
      message_id = factory.Sequence(lambda n: n + 10000)
      from_user = factory.SubFactory('MessageUserFactory')
      chat = factory.SubFactory('MessageChatFactory')
      date = factory.LazyFunction(lambda: int(datetime.now().timestamp()))
      text = factory.Faker('sentence')
  
  class CallbackQueryFactory(factory.Factory):
      id = factory.Faker('uuid4')
      from_user = factory.SubFactory('MessageUserFactory')
      data = factory.Iterator([
          'recipient_edit_1', 'recipient_edit_2', 'add_shared_task_1'
      ])
      message = factory.SubFactory(TelegramMessageFactory)
  ```
- [ ] **Test factory creates valid Telegram objects**
- [ ] **Verify factory handles callback data formats**
- [ ] **Test factory with various message types**
- [ ] **Create factory for error scenarios**
- [ ] **Test factory integration with handlers**
- [ ] **Add factory validation for Telegram constraints**
- [ ] **Test factory with state machine scenarios**
- [ ] **Create factory for complex interaction flows**
- [ ] **Test factory with real Telegram API formats**

#### **2.3 Test File Migration (27 files) - Systematic Approach**

##### **2.3.1 High Priority Files (Core Business Logic)**
- [ ] **`test_workflow_fixes.py` - IMMEDIATE (My mock violations)**
  ```python
  # ❌ BEFORE: Mock-based test (architectural violation)
  @patch('services.recipient_service.UnifiedRecipientRepository')
  def test_shared_recipient_logic(mock_repo):
      mock_repo.return_value.get_recipients_by_user.return_value = [...]
      
  # ✅ AFTER: Factory-based test
  def test_shared_recipient_logic():
      personal_recipient = UnifiedRecipientFactory(is_personal=True)
      shared_recipient = SharedRecipientFactory(is_personal=False)
      # Test with real objects and database
  ```
- [ ] **Remove all `unittest.mock` imports**
- [ ] **Replace mock objects with factory-created real objects**
- [ ] **Test database integration for shared recipient workflow**
- [ ] **Verify test catches actual is_personal=False bug**
- [ ] **Add integration test for complete workflow**
- [ ] **Test screenshot attachment with real task service**
- [ ] **Validate factory-based test finds more bugs than mocks**
- [ ] **Add performance benchmarks vs mock tests**
- [ ] **Document why this test was critical to migrate first**

- [ ] **`test_recipient_service.py` - CRITICAL (Core recipient logic)**
  ```python
  # Migration pattern for service tests
  # ❌ BEFORE: Mocking repository
  @patch('database.repositories.UnifiedRecipientRepository')
  def test_get_recipients_by_user(mock_repo):
      
  # ✅ AFTER: Real repository with test database
  def test_get_recipients_by_user(test_db_session):
      repo = UnifiedRecipientRepository(test_db_session)
      service = RecipientService(repo)
      recipient = UnifiedRecipientFactory.create()
      # Test with real database operations
  ```
- [ ] **Migrate all repository mocks to real database operations**
- [ ] **Use factory-created recipients for all test scenarios**
- [ ] **Test actual database queries and constraints**
- [ ] **Add factory-based edge case testing**
- [ ] **Test real error handling from database**
- [ ] **Verify factory tests catch database corruption bugs**
- [ ] **Add performance testing with large factory datasets**
- [ ] **Test concurrent recipient operations**
- [ ] **Add factory-based security testing**

- [ ] **`test_recipient_task_service.py` - CRITICAL (Task creation workflow)**
  ```python
  # Critical: This service handles the broken shared account workflow
  # ❌ BEFORE: Mock-based task creation testing
  @patch('services.recipient_task_service.create_task_for_recipients')
  
  # ✅ AFTER: Factory-based real workflow testing
  def test_task_creation_shared_vs_personal():
      personal = UnifiedRecipientFactory(is_personal=True)
      shared = SharedRecipientFactory(is_personal=False)
      task = TaskFactory()
      # Test real task creation workflow with both types
  ```
- [ ] **Test real task creation with personal vs shared recipients**
- [ ] **Use TaskFactory for all task creation scenarios**
- [ ] **Test screenshot attachment with real file operations**
- [ ] **Verify factory tests catch shared account creation bugs**
- [ ] **Add factory-based error scenario testing**
- [ ] **Test real platform integration with factories**
- [ ] **Add concurrent task creation testing**
- [ ] **Test real file upload/attachment workflows**
- [ ] **Add factory-based performance testing**

- [ ] **`test_screenshot_attachment_flow.py` - INTEGRATION (End-to-end)**
  ```python
  # This was where mock-based testing FAILED to catch bugs
  # ❌ BEFORE: Mocked create_task_for_recipients hid missing attach_screenshot
  # ✅ AFTER: Real service integration with factory-based data
  def test_screenshot_attachment_real_integration():
      recipient = TodoistRecipientFactory()
      task_data = TaskFactory()
      screenshot_data = ScreenshotDataFactory()
      # Test complete real workflow without mocks
  ```
- [ ] **Remove ALL mocks - use real service implementations**
- [ ] **Test real file operations with factory-generated data**
- [ ] **Verify test catches missing attach_screenshot calls**
- [ ] **Test real error handling in attachment workflow**
- [ ] **Add factory-based edge case testing (large files, etc.)**
- [ ] **Test real platform API integration**
- [ ] **Add factory-based cleanup testing**
- [ ] **Test real transaction rollback scenarios**
- [ ] **Add factory-based security testing for file uploads**

##### **2.3.2 Medium Priority Files (Service Layer)**
- [ ] **`test_parsing_service.py` - Service layer testing**
  - [ ] Replace message parsing mocks with TelegramMessageFactory
  - [ ] Test real parsing logic with factory-generated messages
  - [ ] Add factory-based edge case testing (malformed data)
  - [ ] Test real error handling scenarios
  - [ ] Add factory-based performance testing
  - [ ] Test unicode and special character handling
  - [ ] Add factory-based security testing
  - [ ] Test real state machine integration
  - [ ] Add factory-based regression testing
  - [ ] Document parsing patterns for future factory usage

- [ ] **`test_platforms.py` - Platform integration testing**
  - [ ] Replace platform mocks with PlatformConfigFactory
  - [ ] Test real API configuration with factory data
  - [ ] Add factory-based authentication testing
  - [ ] Test real error scenarios (API limits, network issues)
  - [ ] Add factory-based multi-platform testing
  - [ ] Test real credential validation
  - [ ] Add factory-based performance testing
  - [ ] Test real webhook integration
  - [ ] Add factory-based security testing
  - [ ] Document platform patterns for factory reuse

- [ ] **`test_scheduler.py` - Background task testing**
  - [ ] Replace scheduling mocks with real task scheduling
  - [ ] Test real database operations with factory data
  - [ ] Add factory-based timing and scheduling testing
  - [ ] Test real error handling and retry logic
  - [ ] Add factory-based concurrency testing
  - [ ] Test real cleanup and maintenance operations
  - [ ] Add factory-based performance testing
  - [ ] Test real notification workflows
  - [ ] Add factory-based edge case testing
  - [ ] Document scheduler patterns for factory usage

- [ ] **`test_keyboards.py` - UI component testing**
  - [ ] Replace keyboard mocks with real UI generation
  - [ ] Test real button generation with factory data
  - [ ] Add factory-based internationalization testing
  - [ ] Test real callback data generation
  - [ ] Add factory-based accessibility testing
  - [ ] Test real UI state management
  - [ ] Add factory-based performance testing
  - [ ] Test real navigation flow validation
  - [ ] Add factory-based edge case testing
  - [ ] Document UI patterns for factory reuse

#### **2.4 Migration Verification**
- [ ] **Zero Mock Usage**
  ```bash
  # This should return 0:
  find tests/ -name "*.py" -exec grep -l "unittest.mock" {} \; | wc -l
  ```

- [ ] **100% Factory Usage**
  ```bash  
  # This should return 34:
  find tests/ -name "*.py" -exec grep -l "factory" {} \; | wc -l
  ```

- [ ] **Real Database Integration**
  - [ ] All tests use real database with transactions
  - [ ] Tests catch actual database corruption bugs
  - [ ] Tests verify real service interactions

### Success Criteria
- [ ] **0/34 test files** using `unittest.mock` 
- [ ] **34/34 test files** using Factory Boy
- [ ] **242 test methods** migrated successfully
- [ ] All tests pass with real object integration
- [ ] Tests catch database corruption issues

---

## 🔥 SECTION 3: HIGH PRIORITY - MOCK USAGE ELIMINATION

### Overview
Systematically remove all mock usage and replace with real object testing.

### Implementation Checklist
- [ ] **3.1 Mock Import Removal**
  - [ ] Remove all `from unittest.mock import` statements
  - [ ] Remove all `@patch` decorators  
  - [ ] Remove all `Mock()` object instantiations
  - [ ] Replace with Factory Boy fixtures

- [ ] **3.2 Test Pattern Migration**
  - [ ] Replace `Mock(spec=Class)` with `ClassFactory()`
  - [ ] Replace `mock.return_value = X` with real object creation
  - [ ] Replace `mock.assert_called_with()` with state verification
  - [ ] Add proper database cleanup and isolation

- [ ] **3.3 Service Integration**
  - [ ] Use real service instances with test database
  - [ ] Use real repository instances with transactions  
  - [ ] Test actual container dependency injection
  - [ ] Verify real platform integration where appropriate

### Success Criteria
- [ ] Zero mock objects in test codebase
- [ ] All tests use real objects and database
- [ ] Tests catch real integration issues
- [ ] Test suite execution time acceptable

---

## 🚨 CRITICAL DOS AND DON'TS FOR FACTORY BOY MIGRATION

### ✅ **MANDATORY DOS**

#### **Factory Design Principles**
- ✅ **DO use Factory Boy for ALL domain object creation in tests**
  ```python
  # ✅ CORRECT: Factory-based object creation
  recipient = UnifiedRecipientFactory(is_personal=False)
  task = TaskFactory(title="Test task")
  ```
- ✅ **DO create specialized factory subclasses for specific scenarios**
  ```python
  class SharedRecipientFactory(UnifiedRecipientFactory):
      is_personal = False  # Always shared
  ```
- ✅ **DO use real database operations with factory-created objects**
- ✅ **DO test actual service integration with factories**
- ✅ **DO use factory sequences to avoid ID conflicts**
- ✅ **DO create factory traits for common test patterns**
- ✅ **DO validate factory objects match real domain constraints**
- ✅ **DO use lazy attributes for computed factory values**
- ✅ **DO create factory mixins for reusable patterns**
- ✅ **DO document factory usage patterns for team consistency**

#### **Test Architecture Principles**
- ✅ **DO test with real repository instances and test databases**
- ✅ **DO use factory-created objects for integration testing**
- ✅ **DO test actual database constraints and triggers**
- ✅ **DO verify factory tests catch real bugs (like is_personal=False)**
- ✅ **DO test error scenarios with factory-generated edge cases**
- ✅ **DO use factories for performance testing with large datasets**
- ✅ **DO test concurrent operations with factory-created objects**
- ✅ **DO use factories for security testing scenarios**
- ✅ **DO test real file operations and attachments**
- ✅ **DO verify factory tests work with production-like data**

#### **Migration Process Principles**
- ✅ **DO follow the test-first principle: write failing tests before migration**
- ✅ **DO migrate high-priority files first (core business logic)**
- ✅ **DO verify each migrated test catches more bugs than mock version**
- ✅ **DO run complete test suite after each file migration**
- ✅ **DO document migration patterns for consistency**
- ✅ **DO create factory infrastructure before migrating tests**
- ✅ **DO test factory performance vs mock performance**
- ✅ **DO validate factory objects in production-like environments**
- ✅ **DO create rollback plan for each migration step**
- ✅ **DO update documentation with factory usage examples**

### ❌ **ABSOLUTE DON'TS (FORBIDDEN)**

#### **Mock Usage Violations**
- ❌ **NEVER revert to unittest.mock after Factory Boy migration**
  ```python
  # ❌ FORBIDDEN: Adding new mock usage
  @patch('services.recipient_service.SomeRepository')
  def test_something(mock_repo):  # DON'T DO THIS
  ```
- ❌ **NEVER mix mocks and factories in the same test**
- ❌ **NEVER mock core business logic (services, repositories)**
- ❌ **NEVER use Mock(spec=SomeClass) instead of SomeClassFactory()**
- ❌ **NEVER patch domain objects that can be factory-created**
- ❌ **NEVER mock database operations that factories can test**
- ❌ **NEVER use mock.return_value instead of real object creation**
- ❌ **NEVER skip integration testing in favor of mocking**
- ❌ **NEVER mock file operations that can be tested with real files**
- ❌ **NEVER use mocks to hide implementation bugs**

#### **Factory Anti-Patterns**
- ❌ **NEVER create factories without proper database integration**
  ```python
  # ❌ WRONG: Factory without database consideration
  class BadFactory(factory.Factory):
      id = 1  # Fixed ID will cause conflicts
  ```
- ❌ **NEVER use hardcoded IDs in factories (causes test conflicts)**
- ❌ **NEVER create factories that don't match domain model constraints**
- ❌ **NEVER ignore factory validation for required fields**
- ❌ **NEVER create factories without proper cleanup mechanisms**
- ❌ **NEVER use factories that generate invalid data**
- ❌ **NEVER create factories without considering performance impact**
- ❌ **NEVER skip factory testing in isolation**
- ❌ **NEVER create factories that depend on external state**
- ❌ **NEVER use factories that leak data between tests**

#### **Migration Process Violations**
- ❌ **NEVER migrate tests without running full test suite**
- ❌ **NEVER skip verification that factory tests catch real bugs**
- ❌ **NEVER migrate without documenting the changes**
- ❌ **NEVER ignore test performance degradation after migration**
- ❌ **NEVER skip edge case testing during migration**
- ❌ **NEVER migrate without proper rollback plan**
- ❌ **NEVER ignore integration test failures after migration**
- ❌ **NEVER skip database transaction testing**
- ❌ **NEVER migrate without verifying production compatibility**
- ❌ **NEVER ignore factory infrastructure setup requirements**

### 🔍 **CRITICAL VALIDATION CHECKLIST**

#### **Before Each Migration Step**
- [ ] **Verify Factory Boy infrastructure is ready**
- [ ] **Check test database is properly configured**
- [ ] **Validate factory objects match domain constraints**
- [ ] **Test factory performance is acceptable**
- [ ] **Ensure factory cleanup works correctly**
- [ ] **Verify no factory ID conflicts exist**
- [ ] **Check factory integration with DI container**
- [ ] **Test factory with production-like data volumes**
- [ ] **Validate factory error handling**
- [ ] **Ensure factory documentation is complete**

#### **After Each Migration Step**
- [ ] **All tests pass with factory-based objects**
- [ ] **Factory tests catch bugs that mocks missed**
- [ ] **No performance degradation from factory usage**
- [ ] **Factory objects work with real database constraints**
- [ ] **Integration tests validate end-to-end workflows**
- [ ] **Factory cleanup prevents test pollution**
- [ ] **Documentation reflects factory usage patterns**
- [ ] **Rollback plan is tested and works**
- [ ] **Factory code follows established patterns**
- [ ] **Team can easily use new factory infrastructure**

### 🎯 **SUCCESS INDICATORS**

#### **Technical Success Metrics**
- [ ] **0/34 test files using unittest.mock**
- [ ] **34/34 test files using Factory Boy**
- [ ] **All 242 test methods migrated successfully**
- [ ] **Factory tests catch shared account creation bug**
- [ ] **No false positives from mock behavior**
- [ ] **Real database integration working**
- [ ] **Performance maintained or improved**
- [ ] **Test coverage maintained or increased**
- [ ] **Integration tests validate real workflows**
- [ ] **Factory infrastructure is reusable and maintainable**

#### **Quality Assurance Metrics**
- [ ] **Factory tests catch database corruption bugs**
- [ ] **Real service integration validates workflows**
- [ ] **Error scenarios properly tested with factories**
- [ ] **Edge cases covered with factory-generated data**
- [ ] **Security testing enhanced with factory scenarios**
- [ ] **Performance testing uses realistic factory datasets**
- [ ] **Concurrency testing validates real behavior**
- [ ] **Cleanup and maintenance operations tested**
- [ ] **Documentation supports team factory usage**
- [ ] **Factory patterns established for future development**

---

## 📊 EXECUTION TIMELINE

### **Week 1: Critical Foundation**
1. **Day 1-2:** Shared account bug investigation and fix
2. **Day 3-4:** Factory Boy infrastructure creation
3. **Day 5-7:** Core domain factories (Recipient, Task, Preferences)

### **Week 2: Mass Migration**  
1. **Day 1-3:** High priority test file migration (workflow, recipient, task)
2. **Day 4-5:** Medium priority test file migration (services, platforms)
3. **Day 6-7:** Remaining test file migration and verification

### **Week 3: Verification & Cleanup**
1. **Day 1-2:** Complete mock elimination verification
2. **Day 3-4:** Full test suite execution and debugging
3. **Day 5-7:** Performance optimization and documentation

---

## 🎯 SUCCESS METRICS

### 1. **Shared Account Fix:**
- [ ] `add_shared_recipient` creates `is_personal=False` 
- [ ] Database shows shared recipients correctly
- [ ] Task creation flow shows confirmation buttons
- [ ] No more shared→personal account corruption

### 2. **Factory Boy Migration:**
- [ ] **0%** mock usage (0/34 files)
- [ ] **100%** Factory Boy usage (34/34 files)  
- [ ] **242 test methods** successfully migrated
- [ ] **All tests pass** with real object integration

### 3. **Test Quality:**
- [ ] Tests catch database corruption bugs
- [ ] Tests verify real service interactions
- [ ] Tests use actual database transactions
- [ ] No false positives from mock behavior

### 4. **Architectural Compliance:**
- [ ] No architectural regressions
- [ ] Proper dependency injection usage
- [ ] Real object testing throughout
- [ ] Maintainable test patterns

---

**Plan Version:** 2.0 - COMPREHENSIVE  
**Based on:** PLANNING_TEMPLATE.md + Factory Boy Requirements + Shared Account Investigation  
**Scope:** 27 test files, 242 test methods, 1 critical shared account bug