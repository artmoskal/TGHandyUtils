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

### Fixed Issues from Recent Review:

1. **Shared Mutable State Anti-Pattern** ❌ → ✅
   - **Problem**: Class variable `_registry: Dict[str, type] = {}` shared across instances
   - **Solution**: Lazy initialization with `_get_registry()` method

2. **Open/Closed Principle Violations** ❌ → ✅
   - **Problem**: If/else chains for platform-specific logic
   - **Solution**: Abstract methods `get_token_from_settings()` and `is_configured_static()`

3. **Unsafe String Operations** ❌ → ✅
   - **Problem**: `api_key.split(':')[0]` without bounds checking
   - **Solution**: Proper length validation before array access

4. **Inconsistent Abstraction** ❌ → ✅
   - **Problem**: Platform-specific logic scattered throughout codebase
   - **Solution**: Platform abstractions handle their own configuration logic

## Development Workflow

### Docker Development Flow:
**IMPORTANT**: The bot runs in Docker container, code changes require container rebuild!

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
   docker-compose build bot
   docker-compose up -d
   ```
4. **Verify Changes**: `docker logs infra-bot-1 --tail 20`

### Before Committing:
1. **Run Tests**: `./test.sh` (unit and integration tests)
2. **Code Review**: Self-review against checklist above
3. **Container Rebuild**: `docker-compose build bot && docker-compose up -d`
4. **Container Testing**: Verify changes work in Docker environment
5. **Log Level Check**: Appropriate logging levels used
6. **Security Review**: No sensitive data exposed
7. **Performance Check**: No obvious performance regressions

### Commit Standards:
- **Message Format**: Descriptive, action-oriented (add/fix/update/refactor)
- **Atomic Commits**: One logical change per commit
- **Clean History**: Squash fixup commits before pushing
- **Co-Authoring**: Include Claude co-authoring when applicable

### Testing Practices:
- **Unit Tests**: Test business logic in isolation
- **Integration Tests**: Test component interactions
- **Container Tests**: Test in Docker environment
- **Manual Testing**: Verify UI flows work correctly

### Code Quality Standards:
- **Type Annotations**: All functions properly typed
- **Documentation**: Complex logic documented
- **Error Handling**: All failure paths handled
- **Logging**: Appropriate debug/info/warning/error levels
- **Clean Code**: Self-documenting, minimal comments needed

### Architecture Principles:
- **SOLID Principles**: All five principles followed
- **Design Patterns**: Used appropriately, not over-engineered
- **Dependency Injection**: Services injected via DI container
- **Abstract Interfaces**: Platform logic abstracted behind interfaces
- **Configuration**: Environment-based, no hardcoded values

### Dependency Injection Guidelines:

#### 1. **Container Setup** (`core/container.py`):
```python
from dependency_injector import containers, providers
from core.interfaces import ITaskService, IPartnerService

class ApplicationContainer(containers.DeclarativeContainer):
    # Configuration
    config = providers.Singleton(Config)
    
    # Database
    database_manager = providers.Singleton(DatabaseManager, ...)
    
    # Repositories
    partner_repository = providers.Factory(PartnerRepository, db_manager=database_manager)
    
    # Services
    partner_service = providers.Factory(PartnerService, partner_repo=partner_repository, ...)
    task_service = providers.Factory(TaskService, partner_service=partner_service, ...)
```

#### 2. **Service Injection Patterns**:
- **Constructor Injection**: Primary pattern for service dependencies
- **Optional Dependencies**: Services can gracefully degrade without optional dependencies
- **Interface-Based**: Always inject interfaces, not concrete implementations

#### 3. **Service Access** (`core/container.py`):
```python
@inject
def get_partner_service(
    partner_service: IPartnerService = Provide[ApplicationContainer.partner_service]
) -> IPartnerService:
    return partner_service

# Usage in handlers/services:
from core.container import get_partner_service
partner_service = get_partner_service()
```

#### 4. **Container Integration**:
- **Global Container**: Single container instance (`container = ApplicationContainer()`)
- **Wiring**: Container automatically wires dependencies
- **Lazy Loading**: Services instantiated on first access
- **Singleton vs Factory**: Use Singleton for stateless services, Factory for stateful

#### 5. **Testing with DI**:
```python
# Override dependencies for testing
container.partner_service.override(MockPartnerService())
# Reset after test
container.reset_override()
```

#### 6. **Best Practices**:
- ✅ **Constructor Injection Only**: No property or method injection
- ✅ **Interface Dependencies**: Depend on abstractions, not implementations
- ✅ **Optional Dependencies**: Use `Optional[IService] = None` for optional services
- ✅ **Graceful Degradation**: Services work even if optional dependencies unavailable
- ✅ **No Service Locator**: Don't pass container around, use injection
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