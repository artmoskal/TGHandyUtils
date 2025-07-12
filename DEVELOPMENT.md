# 🛠️ Development Guide

Welcome to the TGHandyUtils development guide! This document will help you get started with contributing to the project.

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Docker & Docker Compose
- Telegram Bot Token
- OpenAI API Key

### Local Development Setup

1. **Clone and setup environment**
   ```bash
   git clone https://github.com/artmoskal/TGHandyUtils.git
   cd TGHandyUtils
   cp .env.example .env  # Edit with your tokens
   ```

2. **Run with Docker (recommended)**
   ```bash
   docker-compose up -d
   docker-compose logs -f bot  # Watch logs
   ```

3. **Run locally (for debugging)**
   ```bash
   conda env create -f environment.yaml
   conda activate TGHandyUtils
   python main.py
   ```

## 🧪 Testing

### Running Tests
```bash
# Run all tests
docker-compose run --rm bot python -m pytest

# Run with coverage
docker-compose run --rm bot python -m pytest --cov

# Run specific test file
docker-compose run --rm bot python -m pytest tests/unit/test_recipient_task_service.py -v

# Run tests matching pattern
docker-compose run --rm bot python -m pytest -k "test_create_task"
```

### Writing Tests
We use pytest and Factory Boy for testing:

```python
# tests/unit/test_my_feature.py
import pytest
from tests.factories import TaskFactory, UnifiedRecipientFactory

def test_my_feature():
    # Arrange
    task = TaskFactory(title="Test Task")
    recipient = UnifiedRecipientFactory(is_default=True)
    
    # Act
    result = my_service.process(task, recipient)
    
    # Assert
    assert result.success
    assert result.message == "Expected message"
```

### Test Organization
- `tests/unit/`: Test individual components in isolation
- `tests/integration/`: Test component interactions
- `tests/factories/`: Factory Boy factories for test data generation

## 📝 Code Style

### Python Style Guide
- Follow PEP 8
- Use type hints for function parameters and returns
- Maximum line length: 120 characters
- Use descriptive variable names

### Imports Order
```python
# Standard library
import os
from datetime import datetime

# Third-party
import pytest
from aiogram import Bot

# Local application
from core.interfaces import ServiceResult
from services.recipient_service import RecipientService
```

### Docstrings
```python
def create_task_for_recipients(self, request: TaskCreationRequest) -> ServiceResult:
    """Create task for specified recipients or defaults.
    
    Args:
        request: TaskCreationRequest object containing all parameters
        
    Returns:
        ServiceResult with success status, message, and optional data
        
    Raises:
        DatabaseError: If database operation fails
    """
```

## 🏗️ Architecture Guidelines

### Adding a New Feature

1. **Plan the implementation**
   - Identify affected components
   - Design interfaces/contracts
   - Consider error cases

2. **Write tests first (TDD)**
   ```python
   def test_new_feature():
       # This test will fail initially
       result = service.new_feature()
       assert result.success
   ```

3. **Implement the feature**
   - Follow existing patterns
   - Use dependency injection
   - Return ServiceResult objects
   - Use parameter objects for 4+ parameters

4. **Update documentation**
   - Add docstrings
   - Update README if user-facing
   - Add to ARCHITECTURE.md if structural

### Common Patterns

#### ServiceResult Pattern
```python
# Always return ServiceResult from services
def my_service_method(self, data: MyData) -> ServiceResult:
    try:
        # Do work
        result = process(data)
        return ServiceResult.success_with_data(
            "Operation successful", 
            result
        )
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        return ServiceResult.failure(
            ErrorMessages.OPERATION_FAILED
        )
```

#### Repository Pattern
```python
# Data access through repositories
class MyRepository:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def get_by_id(self, id: int) -> Optional[MyModel]:
        with self.db_manager.get_connection() as conn:
            # Execute query
            # Return model or None
```

#### Parameter Objects
```python
# For methods with many parameters
@dataclass(frozen=True)
class MyOperationRequest:
    user_id: int
    title: str
    description: str = ""
    options: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.title:
            raise ValueError("title is required")
```

## 🐛 Debugging

### Logging
```python
from core.logging import get_logger

logger = get_logger(__name__)

# Use appropriate levels
logger.debug("Detailed information for debugging")
logger.info("General information")
logger.warning("Warning but not an error")
logger.error("Error occurred but handled")
logger.critical("System-critical error")
```

### Common Issues

1. **Import errors**
   - Check PYTHONPATH includes project root
   - Verify all dependencies installed

2. **Database errors**
   - Check migrations applied
   - Verify foreign key constraints
   - Look for transaction deadlocks

3. **Platform API errors**
   - Check API tokens valid
   - Verify rate limits not exceeded
   - Check network connectivity

### Docker Debugging
```bash
# Enter container shell
docker-compose exec bot bash

# Run Python shell in container
docker-compose exec bot python

# Check database
docker-compose exec bot sqlite3 data/db/tasks.db
```

## 🚀 Deployment

### Production Checklist
- [ ] All tests passing
- [ ] No hardcoded secrets
- [ ] Error messages user-friendly
- [ ] Logging appropriate for production
- [ ] Database migrations ready
- [ ] Documentation updated

### Environment Variables
```bash
# Required
TELEGRAM_BOT_TOKEN=your_token
OPENAI_API_KEY=your_key

# Optional
LOG_LEVEL=INFO  # DEBUG for development
DATABASE_PATH=data/db/tasks.db
```

## 📚 Resources

### Project Conventions
- **Error Messages**: Use `ErrorMessages` class constants
- **HTTP Settings**: Use `HttpConstants` for timeouts/retries
- **Service Returns**: Always return `ServiceResult` objects
- **Testing**: Write tests for new features
- **Commits**: Clear, descriptive commit messages

### Useful Commands
```bash
# Format code
black .

# Check types
mypy .

# Find TODOs
grep -r "TODO" --include="*.py" .

# Database console
sqlite3 data/db/tasks.db
```

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Write tests for your changes
4. Implement your feature
5. Ensure all tests pass
6. Commit changes (`git commit -m 'Add amazing feature'`)
7. Push to branch (`git push origin feature/amazing-feature`)
8. Open a Pull Request

### PR Requirements
- All tests must pass
- Code follows project style
- Documentation updated if needed
- Clear description of changes

## ❓ Getting Help

- Check existing issues on GitHub
- Read through test files for examples
- Email: 4spamartem@gmail.com

Happy coding! 🚀