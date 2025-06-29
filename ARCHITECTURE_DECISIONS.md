# Architecture Decisions - Clean Recipient System

## Current Architecture (Post-Refactoring - June 2025)

### ✅ **Clean Recipient-Based System**
The application now uses a unified **recipient system** that handles all task destinations consistently:

- **User Platforms**: Platforms owned by the user (e.g., "My Todoist", "My Trello")
- **Shared Recipients**: External task destinations (e.g., "Wife's Todoist", "Team Trello")

### ✅ **Unified Data Model**
All recipients are managed through a consistent interface:
```
Recipient {
  id: string (platform_1, shared_2, etc.)
  name: string (display name)
  platform_type: string (todoist, trello)
  type: string (user_platform, shared_recipient)
  enabled: boolean
}
```

## Key Architectural Decisions

### Decision 1: Clean Separation of Concerns
**Decision**: Separate repositories for different data types
- `UserPlatformRepository`: User-owned platforms
- `SharedRecipientRepository`: Shared recipients
- `UserPreferencesV2Repository`: User preferences
- `TaskRepository`: Task data

**Rationale**:
- Single responsibility principle
- Easy to test and maintain
- Clear data ownership
- Proper encapsulation

### Decision 2: Unified Service Layer
**Decision**: `RecipientService` provides unified access to all recipient types
**Rationale**:
- Single interface for handlers
- Consistent behavior
- Easy to extend with new recipient types
- Centralized business logic

### Decision 3: Clean Database Schema
**Decision**: Use `telegram_user_id` directly in all tables, no internal user IDs
**Rationale**:
- Simpler schema
- No foreign key complications
- Direct relationship to Telegram users
- Better performance

### Decision 4: Dependency Injection
**Decision**: Use dependency injection for all services
**Rationale**:
- Testable code
- Loose coupling
- Easy to mock dependencies
- Professional architecture

### Decision 5: State Management
**Decision**: Clean FSM states with proper state clearing
**Rationale**:
- Predictable user flows
- No state contamination
- Clear state transitions
- Better user experience

## Data Flow

### Task Creation Flow
1. User triggers `/create_task`
2. Check if recipient UI is enabled
3. If enabled: Show recipient selection
4. If disabled: Use default recipients
5. Create task on selected recipients' platforms
6. Return success/failure with URLs

### Recipient Management Flow
1. User accesses `/recipients`
2. Show all recipients (user platforms + shared)
3. Allow adding/removing/toggling recipients
4. Changes persist immediately

## Technology Stack

### Core Technologies
- **Aiogram**: Telegram bot framework
- **SQLite**: Database with proper schema
- **dependency-injector**: Dependency injection
- **Python dataclasses**: Type-safe models

### Architecture Patterns
- **Repository Pattern**: Data access abstraction
- **Service Layer**: Business logic encapsulation
- **Dependency Injection**: Loose coupling
- **FSM**: State management for user flows

## Quality Assurance

### Testing Strategy
- Unit tests for all services
- Mock dependencies for isolation
- Clean test fixtures
- Comprehensive coverage

### Code Quality
- Type hints throughout
- Proper error handling
- Logging for debugging
- Clean, readable code

## Performance Considerations

### Database Optimization
- Proper indexes on user_id fields
- Efficient queries
- Connection pooling
- WAL mode for SQLite

### Memory Management
- No global state pollution
- Proper cleanup
- Efficient data structures

## Security

### Data Protection
- No logging of credentials
- Secure token storage
- Proper error handling
- No exposure of internal IDs

## Future Extensions

### Easy to Add
- New platform types (GitHub, Asana, etc.)
- New recipient types
- Additional user preferences
- Advanced scheduling

### Migration Path
- Clean schema allows easy migrations
- No legacy code to maintain
- Proper interfaces for extensions

## Lessons Learned

### What We Fixed
- ❌ Removed dual platform/partner systems
- ❌ Eliminated configuration storage chaos
- ❌ Fixed database integrity issues
- ❌ Cleaned up state management
- ❌ Proper separation of concerns

### Best Practices Applied
- ✅ Single responsibility principle
- ✅ Dependency injection
- ✅ Repository pattern
- ✅ Clean data models
- ✅ Proper error handling
- ✅ Comprehensive testing

## Summary

The new recipient system provides a **clean, maintainable, and extensible** architecture that:
- Handles all task destinations uniformly
- Has proper separation of concerns
- Uses professional design patterns
- Is fully tested and type-safe
- Has zero legacy code contamination

This architecture supports the application's core mission: **making task creation across multiple platforms simple and reliable**.