# Code Quality Improvement Plan

> **⚠️ IMPORTANT: CODE QUALITY REFACTORING PLAN ⚠️**  
> This plan addresses code smells and anti-patterns while preserving existing architecture.  
> Based on comprehensive codebase analysis identifying tuple returns, god classes, and duplication.  
> **Must follow test-first development with existing pytest + Factory Boy infrastructure**

## 🎯 **KEY PRINCIPLES FOR EFFECTIVE PLANNING**

### **🚨 MANDATORY ANALYSIS BEFORE ANY TASK 🚨**
**BEFORE starting ANY implementation, ALWAYS:**
1. **Read existing test files** - Leverage pytest + Factory Boy patterns in `tests/unit/`
2. **Read existing MD files** - Follow DI container and interface patterns already established  
3. **Check for Factory Boy usage** - Already implemented, use existing factories in `tests/factories/`
4. **Verify architectural decisions** - Build on `core/container.py` DI and `core/interfaces.py` abstractions
5. **Document analysis findings** - Record current patterns: dependency-injector, pytest, Factory Boy
6. **Search for similar code** - Found existing result patterns, error handling approaches
7. **Check git history** - Review evolution of service layer and repository patterns
8. **List affected entities** - Services, repositories, handlers, and their corresponding tests

### **Planning Philosophy:**
1. **Start with Diagnosis** - Identified: tuple returns, god classes, string duplication, parameter bloat
2. **Prioritize by Impact** - Fix foundational anti-patterns first (tuple returns affect 20+ methods)
3. **Test-First MANDATORY** - Use existing `./test-dev.sh unit/integration/fast/all` infrastructure
4. **Test Everything** - Build on existing 60%+ coverage, maintain Factory Boy patterns
5. **Document Progress** - Track each anti-pattern fix with measurable improvements
6. **Plan for Rollback** - Git checkpoints after each successful section
7. **NEVER REVERT ARCHITECTURE** - Build on existing DI container, don't replace good patterns
8. **Clean Git History** - Squash commits per section, clear messages about what was improved

### **Test-First Development Protocol:**
**CRITICAL**: NEVER implement changes without failing tests first
1. **Write Failing Test** - Use existing Factory Boy patterns from `tests/factories/`
2. **Verify Test Fails** - Run `./test-dev.sh unit` to confirm test catches the issue
3. **Implement Fix** - Follow existing DI patterns in `core/container.py`
4. **Verify Test Passes** - Run relevant test subset via `./test-dev.sh`
5. **Regression Check** - Run `./test.sh` for full coverage report
6. **Refactor & Document** - Update docstrings, maintain existing code style

### **Quality Gates:**
- ✅ **Comprehensive Logging** - Use existing logger patterns from `core/logging`
- ✅ **Error Handling** - Build on existing error helper patterns
- ✅ **Dependency Injection** - Extend existing `ApplicationContainer` in `core/container.py`
- ✅ **SOLID Principles** - Extract focused services, maintain single responsibility
- ✅ **Test Coverage** - Minimum 60% for modified code using Factory Boy
- ✅ **Type Hints** - Follow existing type annotation patterns
- ✅ **Docstrings** - Match existing documentation style
- ✅ **No Duplication** - Consolidate 80+ duplicate error message strings
- ✅ **Clean Code** - Remove tuple return anti-patterns, fix parameter bloat

### **Planning Structure:**
- **CRITICAL** sections first (tuple returns - foundational anti-pattern)
- **HIGH** priority (god class decomposition)
- **MEDIUM** priority (string deduplication, constants)
- **LOW** priority (method parameter optimization)

---

## 📋 PROGRESS TRACKER

### 🚀 CURRENT STATUS
**Currently Working On:** Creating plan file  
**Last Updated:** 2025-07-12  
**Next Priority:** Section 1 - Tuple Return Anti-Pattern Fix

**✅ COMPLETED:** 
- Initial codebase analysis identifying anti-patterns
- Existing infrastructure review (DI, tests, interfaces)
- Plan creation and structure definition

**🚧 IN PROGRESS:**
- Setting up plan documentation

**📋 PENDING:**
- All implementation sections
- Testing and validation
- Documentation updates

---

## 🔥 SECTION 1: CRITICAL - TUPLE RETURN ANTI-PATTERN FIX

### Overview
The codebase has 20+ methods returning `(bool, str, Optional[Dict])` tuples instead of proper domain objects. This violates clean code principles, makes testing difficult, and reduces type safety. Primary offender is `services/recipient_task_service.py` with methods like `create_task_for_recipients()`, `add_task_to_recipient()`, and `remove_task_from_recipient()` all returning inconsistent tuple structures.

### 📊 Diagnostic Commands
```bash
# Check current tuple return patterns
echo "=== Analyzing tuple returns ==="
grep -rn "return.*,.*,.*" services/ --include="*.py"
grep -rn "return False," services/ --include="*.py" | wc -l
grep -rn "return True," services/ --include="*.py" | wc -l

# Check current test patterns for these methods
grep -rn "create_task_for_recipients" tests/ --include="*.py"
grep -rn "add_task_to_recipient" tests/ --include="*.py"

# Check existing result-like patterns
grep -rn "class.*Result" --include="*.py" .
grep -rn "success.*message" --include="*.py" services/

# Check test coverage before changes
./test-dev.sh unit --cov=services.recipient_task_service --cov-report=term-missing
```

### Root Cause Analysis
```
# PROBLEM: 20+ methods return (bool, str, Optional[Dict]) tuples making code hard to test and maintain
# IMPACT: Callers must unpack tuples, no type safety, difficult to extend return values
# CAUSE: Quick implementation without proper domain modeling, grew organically
# EXISTING CODE: Found some error result patterns in helpers/, existing interfaces in core/
# CAN REUSE: Existing DI container patterns, interface abstractions, Factory Boy test patterns
```

### Detailed Fix Checklist
- [ ] **1.1 Pre-Implementation Analysis**
  - [ ] Map all tuple-returning methods in `services/recipient_task_service.py` (lines 58, 65, 69, 73, 107, 166, 180+)
  - [ ] Document existing calling patterns in handlers and other services
  - [ ] Review existing interface patterns in `core/interfaces.py` for consistency
  - [ ] Check if any result-like classes already exist in helpers/
  - [ ] List all test files that will need updating (`tests/unit/test_recipient_task_service.py`, etc.)
  
- [ ] **1.2 Test Writing Phase**
  - [ ] Create failing tests for new `ServiceResult` class using Factory Boy patterns
  - [ ] Write failing tests for updated method signatures (one method at a time)
  - [ ] Use existing test infrastructure patterns from `tests/unit/test_recipient_service.py`
  - [ ] Verify tests fail correctly with `./test-dev.sh unit`
  - [ ] Add integration tests for handler→service interactions
  
- [ ] **1.3 Implementation Phase**  
  - [ ] Create `IServiceResult` interface in `core/interfaces.py` following existing patterns
  - [ ] Implement `ServiceResult` class with `success`, `message`, `data` fields
  - [ ] Add `ServiceResult` to DI container in `core/container.py` if needed
  - [ ] Convert one method at a time: start with `add_task_to_recipient()` (simpler method)
  - [ ] Update calling code in handlers to use result objects instead of tuple unpacking
  
- [ ] **1.4 Testing & Validation**
  - [ ] All unit tests passing: `./test-dev.sh unit --cov=services.recipient_task_service`
  - [ ] Integration tests passing: `./test-dev.sh integration`
  - [ ] No regressions in existing functionality: `./test.sh`
  - [ ] Manual testing of task creation, addition, removal workflows
  - [ ] Performance check - result objects shouldn't add significant overhead
  
- [ ] **1.5 Cleanup & Review**
  - [ ] Remove any temporary compatibility code
  - [ ] Update docstrings to reflect new return types
  - [ ] Ensure all type hints are correct
  - [ ] Git commit with clear message: "refactor: replace tuple returns with ServiceResult objects"
  - [ ] No tuple returns remaining in modified methods

### Implementation Example
```python
# Follow existing interface pattern in core/interfaces.py
from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass

@dataclass
class ServiceResult:
    """Standard service operation result following existing patterns."""
    success: bool
    message: str
    data: Optional[Any] = None
    
    @classmethod
    def success_with_data(cls, message: str, data: Any) -> 'ServiceResult':
        return cls(True, message, data)
    
    @classmethod
    def failure(cls, message: str) -> 'ServiceResult':
        return cls(False, message, None)

# Update service method (example from recipient_task_service.py:237)
def add_task_to_recipient(self, user_id: int, task_id: int, recipient_id: int) -> ServiceResult:
    """Add existing task to recipient. Returns ServiceResult instead of tuple."""
    # ... existing logic ...
    if success:
        return ServiceResult.success_with_data(
            format_platform_addition_success(recipient.platform_type, recipient.name),
            task_data
        )
    else:
        return ServiceResult.failure(f"❌ Failed to add to {recipient.name}")

# Update calling code in handlers
result = self.recipient_task_service.add_task_to_recipient(user_id, task_id, recipient_id)
if result.success:
    await callback_query.answer(result.message)
else:
    await callback_query.answer(result.message, show_alert=True)
```

### Success Criteria
- [ ] Zero methods return tuples in `services/recipient_task_service.py`
- [ ] All tests passing with 60%+ coverage for modified methods
- [ ] ServiceResult class registered in DI container following existing patterns
- [ ] All calling code updated to use result objects
- [ ] Type safety improved (no more tuple unpacking)
- [ ] Clean git history with single commit per method conversion
- [ ] No performance regression

---

## 🔥 SECTION 2: HIGH - GOD CLASS DECOMPOSITION

### Overview
`UnifiedRecipientRepository` (729 lines, 29+ methods) violates Single Responsibility Principle by handling CRUD operations, validation, JSON serialization, preferences, auth requests, and shared authorizations. This makes testing difficult and violates SOLID principles established in the codebase.

### 📊 Diagnostic Commands
```bash
# Analyze the god class
echo "=== Analyzing UnifiedRecipientRepository ==="
wc -l database/unified_recipient_repository.py
grep -n "def " database/unified_recipient_repository.py | wc -l
grep -n "class " database/unified_recipient_repository.py

# Check method groupings by responsibility
grep -n "def.*preference" database/unified_recipient_repository.py
grep -n "def.*auth" database/unified_recipient_repository.py
grep -n "def.*recipient" database/unified_recipient_repository.py

# Check dependencies on this class
grep -rn "UnifiedRecipientRepository" services/ --include="*.py"
grep -rn "UnifiedRecipientRepository" --include="*.py" core/container.py

# Check existing repository patterns
grep -rn "class.*Repository" database/ --include="*.py"
find tests/ -name "*repository*" -type f
```

### Root Cause Analysis
```
# PROBLEM: 729-line repository doing too many things (CRUD + validation + preferences + auth)
# IMPACT: Hard to test, violates SRP, tight coupling, difficult to maintain
# CAUSE: Grew organically as unified solution, merged multiple responsibilities
# EXISTING CODE: Clean DI patterns in core/container.py, existing repository abstractions
# CAN REUSE: Existing DI registration patterns, interface abstractions, test factories
```

### Detailed Fix Checklist
- [ ] **2.1 Pre-Implementation Analysis**
  - [ ] Map method groups: CRUD (lines 27-200), preferences (201-400), auth (401-500), shared auth (501+)
  - [ ] Document all services depending on `UnifiedRecipientRepository`
  - [ ] Review existing repository patterns in `database/repositories.py`
  - [ ] Check which methods are actually used vs. unused code
  - [ ] Plan DI container updates needed in `core/container.py`
  
- [ ] **2.2 Test Writing Phase**
  - [ ] Write failing tests for new `PreferencesRepository` using existing Factory Boy patterns
  - [ ] Write failing tests for new `AuthRequestRepository` 
  - [ ] Use existing test patterns from `tests/unit/test_recipient_service.py`
  - [ ] Ensure test coverage for each extracted repository
  - [ ] Integration tests for service layer still working with new repositories
  
- [ ] **2.3 Implementation Phase**  
  - [ ] Extract `IUserPreferencesRepository` interface in `core/interfaces.py`
  - [ ] Implement `UserPreferencesRepository` with focused responsibility
  - [ ] Register new repository in DI container following existing patterns
  - [ ] Extract `IAuthRequestRepository` interface and implementation
  - [ ] Update services to use specific repositories instead of unified one
  
- [ ] **2.4 Testing & Validation**
  - [ ] All repository tests passing independently
  - [ ] Service layer tests still passing with new dependencies
  - [ ] Integration tests for full workflows working
  - [ ] No duplicate code between extracted repositories
  - [ ] Performance comparable to original unified repository
  
- [ ] **2.5 Cleanup & Review**
  - [ ] Remove unused methods from original repository
  - [ ] Update DI container configuration
  - [ ] Clean imports across codebase
  - [ ] Update documentation for new repository structure
  - [ ] Git commit per extracted repository

### Implementation Example
```python
# Extract focused repository following existing patterns
class IUserPreferencesRepository(ABC):
    """Interface for user preferences operations."""
    
    @abstractmethod
    def get_preferences(self, user_id: int) -> Optional[UnifiedUserPreferences]:
        pass
    
    @abstractmethod
    def update_preferences(self, user_id: int, updates: UnifiedUserPreferencesUpdate) -> bool:
        pass

class UserPreferencesRepository:
    """Focused repository for user preferences only."""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_preferences(self, user_id: int) -> Optional[UnifiedUserPreferences]:
        # Extract specific methods from original repository
        # Follow existing database patterns
        pass

# Update DI container (core/container.py)
user_preferences_repository = providers.Factory(
    UserPreferencesRepository,
    db_manager=database_manager
)

# Update services to use specific repositories
class RecipientService:
    def __init__(self, 
                 repository: UnifiedRecipientRepository,
                 preferences_repo: UserPreferencesRepository):
        self.repository = repository
        self.preferences_repo = preferences_repo
```

### Success Criteria
- [ ] `UnifiedRecipientRepository` reduced to <400 lines
- [ ] Clear separation of concerns (CRUD, preferences, auth in separate repos)
- [ ] All extracted repositories have 60%+ test coverage
- [ ] DI container properly configured for new repositories
- [ ] No duplicate code between repositories
- [ ] Services updated to use appropriate specific repositories
- [ ] Performance within 10% of original implementation

---

## 🔥 SECTION 3: MEDIUM - STRING DEDUPLICATION & CONSTANTS

### Overview
Found 80+ duplicate error message strings (❌ pattern) across 15+ files and hardcoded magic numbers (timeout=30, retry counts). This creates maintenance burden and inconsistent user experience.

### 📊 Diagnostic Commands
```bash
# Check string duplication
echo "=== Analyzing string duplication ==="
grep -rn "❌.*" --include="*.py" . | wc -l
grep -rn "❌ Error" --include="*.py" . | sort | uniq -c | sort -nr
grep -rn "timeout=30" --include="*.py" .
grep -rn "max_retries=3" --include="*.py" .

# Check existing constants patterns
grep -rn "class.*Constants" --include="*.py" .
grep -rn "HTTP_TIMEOUT\|TIMEOUT" --include="*.py" config/
find . -name "constants.py" -o -name "config.py"
```

### Root Cause Analysis
```
# PROBLEM: 80+ duplicate error strings, magic numbers scattered throughout codebase
# IMPACT: Inconsistent messages, hard to maintain, no central message management
# CAUSE: Copy-paste development, no initial constants strategy
# EXISTING CODE: Some config patterns in config.py, existing error helpers
# CAN REUSE: Existing config structure, error helper patterns, established imports
```

### Detailed Fix Checklist
- [ ] **3.1 Pre-Implementation Analysis**
  - [ ] Catalog all duplicate error messages and their frequencies
  - [ ] Document existing config patterns in `config.py`
  - [ ] Check existing error helper patterns in `helpers/`
  - [ ] List all files using magic numbers (timeout, retries, etc.)
  - [ ] Plan import updates needed across codebase
  
- [ ] **3.2 Test Writing Phase**
  - [ ] Write tests ensuring error messages are consistent
  - [ ] Test configuration loading for new constants
  - [ ] Verify existing functionality unchanged with constants
  - [ ] Use existing test patterns for configuration testing
  - [ ] Test error message accessibility from all modules
  
- [ ] **3.3 Implementation Phase**  
  - [ ] Create `ErrorMessages` class in `helpers/error_messages.py`
  - [ ] Add HTTP/timeout constants to existing `config.py`
  - [ ] Replace most frequent duplicate strings first (❌ No recipients, ❌ Failed to create)
  - [ ] Update platform classes to use timeout constants
  - [ ] Update import statements across affected files
  
- [ ] **3.4 Testing & Validation**
  - [ ] All tests passing with constant usage
  - [ ] Error messages consistent across all modules
  - [ ] Configuration properly loaded in all contexts
  - [ ] No hardcoded strings/numbers in modified files
  - [ ] Performance impact negligible
  
- [ ] **3.5 Cleanup & Review**
  - [ ] Remove all duplicate string literals
  - [ ] Ensure all magic numbers replaced with named constants
  - [ ] Update documentation for new constants usage
  - [ ] Clean import statements
  - [ ] Git commit with clear categorization

### Implementation Example
```python
# helpers/error_messages.py
class ErrorMessages:
    """Centralized error messages for consistent UX."""
    
    # Task creation errors
    NO_RECIPIENTS = "❌ No recipients configured. Please add accounts first."
    TASK_CREATION_FAILED = "❌ Failed to create task in database."
    RECIPIENT_NOT_FOUND = "❌ Recipient not found"
    RECIPIENT_DISABLED = "❌ {name} is disabled"
    
    # Platform errors
    PLATFORM_CONNECTION_FAILED = "❌ Could not connect to {platform}"
    TASK_ADD_FAILED = "❌ Failed to add to {recipient}"

# config.py additions
class Config:
    # Existing config...
    
    # HTTP timeouts
    HTTP_TIMEOUT = 30
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2.0

# Usage in services
from helpers.error_messages import ErrorMessages

def add_task_to_recipient(self, user_id: int, task_id: int, recipient_id: int):
    if not recipient:
        return ServiceResult.failure(ErrorMessages.RECIPIENT_NOT_FOUND)
    if not recipient.enabled:
        return ServiceResult.failure(
            ErrorMessages.RECIPIENT_DISABLED.format(name=recipient.name)
        )
```

### Success Criteria
- [ ] 80%+ reduction in duplicate error strings
- [ ] All magic numbers replaced with named constants
- [ ] Consistent error message formatting across modules
- [ ] No performance impact from constants usage
- [ ] Clean import structure maintained
- [ ] Error messages easily maintainable from central location

---

## 🔥 SECTION 4: LOW - METHOD PARAMETER OPTIMIZATION

### Overview
Several methods have 5+ parameters indicating poor abstraction. Primary example: `_create_platform_task(recipient, title, description, due_time, screenshot_data)` in `services/recipient_task_service.py:280`.

### 📊 Diagnostic Commands
```bash
# Find methods with many parameters
echo "=== Analyzing parameter counts ==="
grep -rn "def.*(" --include="*.py" services/ | grep -E "\(.*,.*,.*,.*,.*"
grep -rn "_create_platform_task" --include="*.py" .

# Check existing data class patterns
grep -rn "@dataclass" --include="*.py" .
grep -rn "class.*Data" --include="*.py" models/
find . -name "*_data.py" -o -name "data_*.py"
```

### Root Cause Analysis
```
# PROBLEM: Methods with 5+ parameters, poor abstraction of related data
# IMPACT: Hard to call, test, and maintain; unclear parameter relationships
# CAUSE: Incremental parameter addition without refactoring
# EXISTING CODE: Some dataclass usage in models/, existing data structures
# CAN REUSE: Existing dataclass patterns, model structures, type hints
```

### Detailed Fix Checklist
- [ ] **4.1 Pre-Implementation Analysis**
  - [ ] Identify all methods with 5+ parameters
  - [ ] Group related parameters into logical data objects
  - [ ] Check existing dataclass patterns in `models/`
  - [ ] Document all calling sites that need updates
  - [ ] Plan backwards compatibility if needed
  
- [ ] **4.2 Test Writing Phase**
  - [ ] Write tests for new data classes using Factory Boy
  - [ ] Test method calls with data objects vs individual parameters
  - [ ] Ensure existing tests pass with updated signatures
  - [ ] Use existing Factory Boy patterns for data creation
  - [ ] Test parameter validation in data objects
  
- [ ] **4.3 Implementation Phase**  
  - [ ] Create `TaskCreationData` dataclass for task-related parameters
  - [ ] Update method signatures to accept data objects
  - [ ] Add validation to data classes if needed
  - [ ] Update all calling sites to use data objects
  - [ ] Follow existing type hinting patterns
  
- [ ] **4.4 Testing & Validation**
  - [ ] All tests passing with new signatures
  - [ ] Data objects properly validated
  - [ ] Method calls more readable and maintainable
  - [ ] No performance regression
  - [ ] Type safety improved
  
- [ ] **4.5 Cleanup & Review**
  - [ ] Remove any compatibility shims
  - [ ] Update docstrings for new signatures
  - [ ] Ensure consistent data object usage
  - [ ] Clean type imports
  - [ ] Git commit per method group updated

### Implementation Example
```python
# models/task_data.py
from dataclasses import dataclass
from typing import Optional, Dict

@dataclass
class TaskCreationData:
    """Data object for task creation parameters."""
    title: str
    description: str
    due_time: str
    screenshot_data: Optional[Dict] = None
    
    def __post_init__(self):
        """Validate required fields."""
        if not self.title or not self.due_time:
            raise ValueError("Title and due_time are required")

# Updated method signature
def _create_platform_task(self, recipient: UnifiedRecipient, task_data: TaskCreationData) -> ServiceResult:
    """Create task on platform using data object."""
    # Method body unchanged, just accessing task_data.title, task_data.description, etc.
    pass

# Updated calling code
task_data = TaskCreationData(
    title=title,
    description=description, 
    due_time=due_time,
    screenshot_data=screenshot_data
)
result = self._create_platform_task(recipient, task_data)
```

### Success Criteria
- [ ] No methods with >4 parameters
- [ ] Related parameters grouped into logical data objects
- [ ] All data objects properly typed and validated
- [ ] Method calls more readable and maintainable
- [ ] Test coverage maintained with Factory Boy patterns
- [ ] Type safety improved throughout

---

## 🛠️ DIAGNOSTIC COMMANDS REFERENCE

### Check Current Anti-Patterns
```bash
# Tuple returns
grep -rn "return.*,.*,.*" services/ --include="*.py" | head -10

# Large classes
find . -name "*.py" -exec wc -l {} + | sort -nr | head -10

# String duplication  
grep -rn "❌.*" --include="*.py" . | sort | uniq -c | sort -nr | head -10

# Parameter bloat
grep -rn "def.*(" --include="*.py" services/ | grep -E "\(.*,.*,.*,.*,.*"
```

### Validate Existing Architecture
```bash
# Check DI container
grep -rn "providers\." core/container.py
grep -rn "@inject" --include="*.py" .

# Verify Factory Boy usage
find tests/factories/ -name "*.py" | head -10
grep -rn "Factory" tests/ --include="*.py" | head -5

# Check interface patterns
grep -rn "class.*ABC" core/interfaces.py
grep -rn "abstractmethod" core/interfaces.py | wc -l
```

### Test Infrastructure
```bash
# Available test commands
./test-dev.sh unit                    # Fast unit tests
./test-dev.sh integration            # Integration tests  
./test-dev.sh fast                   # Quick smoke tests
./test.sh                           # Full suite with coverage

# Coverage check
./test-dev.sh unit --cov=services --cov-report=term-missing
```

---

## 🎯 SUCCESS METRICS

### Code Quality Improvements
- [ ] Zero methods returning tuples (from 20+ currently)
- [ ] Largest class <400 lines (from 729-line god class)
- [ ] 80%+ reduction in duplicate error strings (from 80+ found)
- [ ] No methods with >4 parameters (from several 5+ parameter methods)
- [ ] Test coverage >= 60% for all modified code
- [ ] No performance regressions

### Architecture & Process
- [ ] All changes follow existing DI patterns
- [ ] Factory Boy test patterns maintained and extended
- [ ] Proper interfaces created following existing patterns
- [ ] Clean git history with logical commits
- [ ] No breaking changes to public APIs
- [ ] Documentation updated for new patterns

### Maintainability
- [ ] Single Responsibility Principle followed
- [ ] Error messages centrally managed
- [ ] Constants eliminate magic numbers
- [ ] Type safety improved throughout
- [ ] Code duplication eliminated

---

## 🔄 ROLLBACK PLAN

### Per-Section Rollback
```bash
# Rollback Section 1 (tuple returns)
git checkout HEAD~1 -- services/recipient_task_service.py core/interfaces.py
./test-dev.sh unit  # Verify clean state

# Rollback Section 2 (repository extraction)  
git checkout HEAD~3 -- database/ core/container.py services/
./test-dev.sh integration  # Verify clean state

# Full rollback if needed
git checkout main
git branch -D feature/code-quality-improvements
```

### Verification Commands
```bash
# Verify clean state after rollback
./test.sh  # Full test suite must pass
docker-compose restart  # Ensure runtime works
grep -rn "return.*,.*,.*" services/ | wc -l  # Should show original count
```

---

**Plan Version:** 1.0  
**Based on:** Comprehensive codebase analysis + PLANNING_TEMPLATE.md  
**Architecture:** Build on existing DI container, pytest+Factory Boy, interface patterns  
**Timeline:** 10-15 hours across 4 sections with proper testing at each step