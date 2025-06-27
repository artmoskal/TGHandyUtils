# Platform Extension Guide

## Architecture Overview

The platform system uses proper OOP patterns for extensibility:

1. **Abstract Base Class Pattern**: All platforms inherit from `AbstractTaskPlatform`
2. **Factory Pattern with Registry**: `TaskPlatformFactory` uses a registry instead of if/else
3. **Decorator Pattern**: `@register_platform` decorator for automatic registration
4. **Open/Closed Principle**: Open for extension (new platforms), closed for modification

## Adding a New Platform

### Step 1: Create Platform Implementation

Create a new file in the `platforms` directory:

```python
# platforms/notion.py
from platforms.base import AbstractTaskPlatform, register_platform

@register_platform('notion')  # Auto-registers with factory
class NotionPlatform(AbstractTaskPlatform):
    def __init__(self, api_token: str):
        self.api_token = api_token
        # Initialize platform-specific settings
    
    def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        # Implement task creation
        pass
    
    # Implement all other abstract methods...
```

### Step 2: Import in __init__.py

Add import to `platforms/__init__.py`:

```python
from platforms.notion import NotionPlatform  # Triggers registration
```

### Step 3: Platform-Specific Configuration (Optional)

For complex authentication (like Trello's board/list selection), extend the handlers:

1. Add platform-specific state handling if needed
2. The generic handlers already support basic token input
3. Platform settings are stored as `{platform}_token` by default

## How It Works

### Registration Flow
1. Platform class is decorated with `@register_platform('name')`
2. When imported, decorator registers class with factory
3. Factory maintains a registry dictionary (no if/else)
4. `get_platform()` looks up class in registry and instantiates

### Configuration Storage
- Tokens stored as `{platform}_token` in platform_settings
- Additional data stored as `{platform}_{key}`
- Repository automatically detects configured platforms

### UI Generation
- `get_platform_config_keyboard()` dynamically creates buttons for all registered platforms
- Configuration status checked generically
- No hardcoded platform names in UI

## Benefits

1. **No Code Modification**: Add platforms without changing existing code
2. **Type Safety**: Abstract base class ensures interface compliance
3. **Automatic UI**: New platforms automatically appear in configuration
4. **Testability**: Each platform can be tested independently
5. **Maintainability**: Platform logic isolated in separate modules

## Example Platforms

See `example_new_platform.py.example` for a complete example of adding Asana support.