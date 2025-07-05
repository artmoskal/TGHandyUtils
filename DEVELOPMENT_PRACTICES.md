# Development Practices & Code Review Guidelines

## Senior Developer Peer Review Checklist

### Anti-Patterns and Code Issues to Check:

#### 1. **SOLID Principles Violations**
- ✅ **Single Responsibility Principle**: Each class/method has one clear purpose
- ✅ **Open/Closed Principle**: Open for extension, closed for modification
- ✅ **Liskov Substitution Principle**: Subtypes must be substitutable for base types
- ✅ **Interface Segregation Principle**: Clients shouldn't depend on unused interfaces
- ✅ **Dependency Inversion Principle**: Depend on abstractions, not concretions

#### 2. **Design Pattern Implementation**
- ✅ **Factory Pattern**: No if/else chains, use registry pattern
- ✅ **Abstract Factory**: Platform-specific logic abstracted properly
- ✅ **Decorator Pattern**: Self-registering components
- ✅ **Repository Pattern**: Data access abstracted from business logic

#### 3. **Error Handling & Logging**
- ✅ **Exception Handling**: All external calls wrapped in try/catch
- ✅ **Error Messages**: Descriptive, actionable error messages
- ✅ **Logging Levels**: Debug, info, warning, error used appropriately
- ✅ **No Silent Failures**: All failures logged and handled gracefully

#### 4. **Data Management**
- ✅ **Database Transactions**: Atomic operations where needed
- ✅ **Data Validation**: Input validation at boundaries
- ✅ **JSON Parsing**: Safe parsing with error handling
- ✅ **Data Consistency**: Multi-platform data stored consistently

#### 5. **Security Concerns**
- ✅ **Token/Credential Safety**: No hardcoded credentials, secure storage
- ✅ **Input Validation**: All user inputs validated/sanitized
- ✅ **SQL Injection Prevention**: Parameterized queries only
- ✅ **Information Leakage**: No sensitive data in logs

#### 6. **Performance Considerations**
- ✅ **Efficient Queries**: No N+1 problems, optimized database access
- ✅ **Memory Management**: Proper resource cleanup
- ✅ **Async Operations**: Non-blocking I/O where appropriate
- ✅ **Caching**: Appropriate use of caching mechanisms

#### 7. **Code Quality**
- ✅ **DRY Principle**: No duplicate code blocks
- ✅ **Magic Numbers**: All constants properly named
- ✅ **Naming Conventions**: Clear, descriptive names
- ✅ **Method Complexity**: Methods under 20 lines when possible
- ✅ **Type Hints**: All function parameters and returns typed
- ✅ **Documentation**: Critical functions documented

#### 8. **Architectural Concerns**
- ✅ **Loose Coupling**: Components minimally dependent
- ✅ **No Circular Dependencies**: Clean dependency graph
- ✅ **Layered Architecture**: Clear separation of concerns
- ✅ **Single Source of Truth**: No conflicting data sources

#### 9. **Anti-Pattern Prevention**
- ❌ **Shared Mutable State**: Never use class variables for mutable data shared across instances
- ❌ **Hard-Coded Logic**: Avoid if/else chains for extensible behavior; use abstractions
- ❌ **Unsafe Operations**: Always validate array bounds, string operations, and data access
- ❌ **Scattered Logic**: Keep related functionality centralized, not spread across files

## Development Workflow

### Docker Development Flow:
**CRITICAL**: ALL development happens in Docker! NEVER run code on host machine!

1. **Local Development**: Make changes to source files
2. **Hot Copy (Debug Only)**: 
   ```bash
   # Quick copy for debugging (temporary, lost on restart)
   docker cp handlers.py infra-bot-1:/app/handlers.py
   docker exec infra-bot-1 pkill -f python  # Force restart
   ```
3. **Proper Rebuild** (Required for permanent changes):
   ```bash
   # Rebuild container with new code
   docker-compose -f infra/docker-compose.yml build bot
   docker-compose -f infra/docker-compose.yml up -d
   ```
4. **Verify Changes**: `docker logs infra-bot-1 --tail 20`
5. **Run Tests**: ALWAYS use Docker for testing - see Test Commands section

### Before Committing:
1. **Test-First Bug Fixes**: If fixing bugs, ensure failing test exists BEFORE fix
2. **Run Tests**: `./test.sh` (unit and integration tests)
3. **Code Review**: Self-review against checklist above
4. **Container Rebuild**: `docker-compose build bot && docker-compose up -d`
5. **Container Testing**: Verify changes work in Docker environment

### Test-First Bug Fix Protocol:
**MANDATORY**: For ALL bug fixes, this sequence is required:
1. **Reproduce Bug**: Write test that demonstrates the bug and fails
2. **Verify Failure**: Run test to confirm it catches the issue
3. **Fix Bug**: Make minimal code changes to resolve the issue
4. **Verify Success**: Run test to confirm fix works
5. **Regression Check**: Run full test suite to ensure no other issues

### Test Infrastructure vs Functional Issues:
**CRITICAL**: Always distinguish between real bugs and test infrastructure problems
- **Test Failures ≠ Broken Functionality**: Verify actual application behavior first
- **Integration Tests First**: More reliable than complex unit test mocks
- **Mock Patch Debugging**: Function imports inside methods need special patching
- **Manual Verification**: Always test real functionality when tests fail
5. **Log Level Check**: Appropriate logging levels used
6. **Security Review**: No sensitive data exposed
7. **Performance Check**: No obvious performance regressions
8. **Callback Handler Conflicts**: Check for conflicting aiogram callback patterns

#### **Callback Handler Pattern Conflicts**:
- **Issue**: Overlapping lambda filters can cause routing conflicts
- **Example**: `c.data.startswith("toggle_recipient_")` matches `"toggle_recipient_ui"`
- **Solution**: Use exclusion patterns: `c.data.startswith("toggle_recipient_") and c.data != "toggle_recipient_ui"`
- **Best Practice**: Order handlers from most specific to most general

### Commit Standards:
- **Message Format**: Descriptive, action-oriented (add/fix/update/refactor)
- **Atomic Commits**: One logical change per commit
- **Clean History**: Squash fixup commits before pushing
- **Co-Authoring**: Include Claude co-authoring when applicable

### Testing Practices:
- **Unit Tests**: Test business logic in isolation (fast, no external dependencies)
- **Integration Tests**: Test component interactions (database, multi-service)
- **OpenAI Integration Tests**: Real API calls for parsing/timezone logic (token-consuming, expensive)
- **Container Tests**: Test in Docker environment
- **Manual Testing**: Verify UI flows work correctly

#### **Test Commands**:
**IMPORTANT**: Tests run in isolated Docker containers for consistency!

```bash
# Unit tests (fast, no external dependencies) - ISOLATED CONTAINER
./test-dev.sh unit

# Integration tests (COSTS MONEY! Uses real APIs) - ISOLATED CONTAINER
./test-integration.sh                                    # All integration tests
./test-integration.sh test_scheduling_validation.py      # Specific test file
./test-integration.sh test_scheduling_validation.py "-k midnight"  # Specific test by name

# All tests with coverage - ISOLATED CONTAINER
./test.sh

# Running tests in live bot container (NOT RECOMMENDED)
docker exec infra-bot-1 /bin/bash -c "cd /app && source activate TGHandyUtils && pytest tests/unit/ -v"
```

**Integration Test Requirements**:
- ✅ Real `.env` file with valid API keys (OPENAI_API_KEY, etc.)
- ✅ Uses `docker-compose.test-integration.yml` for consistent environment
- ✅ Automatically loads environment variables from `.env`
- ✅ Supports running specific tests or test patterns
- ✅ Provides clear error messages if configuration missing

#### **Test Design Principles**:
- **DST Independence**: Tests handle seasonal timezone changes automatically
- **Real API Testing**: Integration tests use actual external services (no mocking for integration)
- **Unit Test Isolation**: Unit tests mock external dependencies for speed
- **Timezone Flexibility**: Tests accept multiple timezone scenarios and DST variations
- **Prompt Validation**: Tests use actual templates, not duplicated logic
- **Time-Sensitive Testing**: Test edge cases around day boundaries and past/future time parsing
- **External Service Coverage**: Include comprehensive test scenarios for critical external API integrations

### LLM Prompt Engineering Principles:

#### **Temporal Logic in LLM Prompts**:
- **Explicit Past-Time Handling**: LLM prompts must explicitly define behavior for past times within same day
- **Future-Forward Scheduling**: Always instruct LLM to schedule for future times unless explicitly requesting past times
- **Ambiguity Resolution**: Add CRITICAL rules for edge cases like "today [time]" when that time has passed
- **Integration Testing**: Use real LLM API calls (not mocks) to test prompt changes with actual time conditions

### Testing Principles:

#### **Bug Prevention Strategy**:
1. **Test Production Services**: Always test the actual service classes used in production, not legacy/unused services
2. **Integration Over Mocks**: Include integration tests that use real repositories to catch missing methods and interface mismatches
3. **User Journey Coverage**: Test complete user workflows, not just isolated methods
4. **Coverage Monitoring**: Ensure all public methods have at least basic test coverage
5. **Interface Validation**: Test that required methods exist on dependencies (avoid "method not found" errors)

#### **Testing Checklist**:
- [ ] All public methods have unit tests
- [ ] Integration tests cover critical user journeys  
- [ ] Service tests match production service usage
- [ ] Repository tests verify all required methods exist
- [ ] UI action handlers have test coverage
- [ ] End-to-end tests validate complete workflows

#### **Bug Fix Protocol**:
**MANDATORY**: After fixing any bug, you MUST:
1. **Write Test**: Create minimal but comprehensive test (unit or integration) that would have caught the bug
2. **Run Full Suite**: Execute `./test.sh` to ensure no regressions
3. **Verify Pass Rate**: Target 95%+ pass rate, investigate failures
4. **Keep it Minimal**: Write the smallest test that validates the fix

**Test Selection Guide**:
- **Unit Test**: For logic errors, validation failures, method-level bugs
- **Integration Test**: For service interaction bugs, dependency injection issues, workflow failures
- **Always**: Choose the minimal test type that would catch the specific bug

### Documentation Standards:
- **Principles Only**: Document universal principles, not specific cases or implementation details
- **Future-Focused**: Include only conclusions useful for future development decisions
- **No Concrete Examples**: Avoid cluttering with specific bug cases, dates, or technical details
- **Universal Application**: Ensure documented principles apply beyond current context

### Code Quality Standards:
- **Type Annotations**: All functions properly typed
- **Documentation**: Complex logic documented
- **Error Handling**: All failure paths handled
- **Logging**: Comprehensive multi-level logging with workflow tracking
- **Clean Code**: Self-documenting, minimal comments needed
- **No Dead Code**: Remove unused functions, commented-out code, and broken implementations immediately

### Logging Standards:
- **Format**: Includes function name and line numbers for debugging
- **Workflow Logging**: Entry/exit of critical functions with parameters
- **State Logging**: Application state changes (preferences, recipients)
- **Database Logging**: All DB operations with query details and results
- **Error Logging**: Full exception traces with context

### Architecture Principles:
- **SOLID Principles**: All five principles followed
- **Design Patterns**: Used appropriately, not over-engineered
- **Dependency Injection**: Services injected via DI container
- **Abstract Interfaces**: Platform logic abstracted behind interfaces
- **Configuration**: Environment-based, no hardcoded values

### Dependency Injection Principles:
- ✅ **Constructor Injection Only**: No property or method injection
- ✅ **Interface Dependencies**: Depend on abstractions, not implementations
- ✅ **Optional Dependencies**: Use `Optional[IService] = None` for optional services
- ✅ **Graceful Degradation**: Services work even if optional dependencies unavailable
- ✅ **No Service Locator**: Don't pass container around, use injection
- ✅ **Container Patterns**: Single global container, lazy loading, appropriate lifetime management
- ✅ **Test Overrides**: Support dependency overriding for testing scenarios
- ❌ **Avoid Circular Dependencies**: Service A shouldn't depend on service B if B depends on A

### Platform Extension:
- **New Platform Addition**: Follow `platforms/README_PLATFORM_EXTENSION.md`
- **No Code Modification**: Existing code unchanged when adding platforms
- **Registry Pattern**: Self-registering via decorators
- **Interface Compliance**: All abstract methods implemented

## Review Process:
1. Create checklist of potential issues
2. Review code systematically against checklist
3. Fix identified issues before committing
4. Test in container environment
5. Commit with descriptive message and co-authoring
6. Document any new patterns or practices

This process ensures high code quality, maintainability, and extensibility.