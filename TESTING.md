# Testing Guide

This document describes the unified testing system for TGHandyUtils.

## 🧪 **Unified Test Runner**

The project uses a single unified test runner that handles both unit and integration tests in a consistent environment.

### **Quick Start**

```bash
# Run all unit tests (fast, no API costs)
./run-tests.sh unit

# Run all integration tests (requires .env with real API keys)
./run-tests.sh integration

# Run everything (comprehensive test suite)
./run-tests.sh all
```

### **Advanced Usage**

```bash
# Specific test files
./run-tests.sh unit test_models.py
./run-tests.sh integration test_screenshot_processing.py

# Specific test classes or methods
./run-tests.sh unit "test_models.py::TestTaskModels"
./run-tests.sh unit "test_models.py::TestTaskModels::test_task_create"

# Filter tests by keyword
./run-tests.sh integration test_parsing.py -k "midnight"
./run-tests.sh unit test_timezone.py -k "cascais"

# Verbose output
./run-tests.sh unit --verbose
./run-tests.sh integration test_screenshot.py -v

# Multiple pytest arguments
./run-tests.sh unit test_models.py -v --tb=short
```

## 🏗️ **Test Architecture**

### **Environment Consistency**
- **Same Docker Environment**: Both unit and integration tests use identical Docker setup
- **Production-Like**: Tests run in same environment as production bot
- **Consistent Dependencies**: Same Python packages, versions, and configurations

### **Environment Variables Strategy**

| Test Type | Environment | API Keys | Purpose |
|-----------|-------------|----------|---------|
| **Unit** | Mock tokens | `OPENAI_API_KEY=test_key_not_used` | Fast, isolated, no API costs |
| **Integration** | Real APIs | Loaded from `.env` file | End-to-end validation with real services |

### **Test Organization**

```
tests/
├── unit/                    # Fast isolated tests with mocked dependencies
│   ├── test_models.py      # Model validation and business logic
│   ├── test_services.py    # Service layer with mocked externals
│   └── test_platforms.py   # Platform adapters with mocked APIs
└── integration/            # End-to-end tests with real external services
    ├── test_parsing.py     # Real OpenAI API calls for parsing
    ├── test_screenshot.py  # Real image processing workflows
    └── test_timezone.py    # Real timezone/datetime edge cases
```

## 📊 **Test Reporting**

The unified runner generates consistent reports for all test types:

### **Coverage Reports**
- **HTML**: `infra/test-results/htmlcov-unit/index.html`
- **HTML**: `infra/test-results/htmlcov-integration/index.html`
- **XML**: `infra/test-results/coverage-unit.xml`
- **XML**: `infra/test-results/coverage-integration.xml`

### **Test Results**
- **JUnit XML**: `infra/test-results/junit-unit.xml`
- **JUnit XML**: `infra/test-results/junit-integration.xml`

## 🔧 **Setup Requirements**

### **Unit Tests** (No Setup Required)
- No external dependencies
- Uses mock tokens and data
- Runs completely offline

### **Integration Tests** (Requires Setup)

1. **Create `.env` file** in project root:
```bash
OPENAI_API_KEY=your-actual-openai-api-key
TELEGRAM_BOT_TOKEN=your-bot-token  # Optional for most tests
```

2. **Verify setup**:
```bash
./run-tests.sh integration --help  # Should not show .env errors
```

## 📈 **Performance Guidelines**

### **Unit Tests**
- **Target**: < 30 seconds total runtime
- **Parallelization**: Safe to run in parallel
- **CI/CD**: Run on every commit
- **Coverage**: Aim for >80% line coverage

### **Integration Tests**
- **Target**: < 5 minutes total runtime
- **API Costs**: ~$0.01-0.10 per full run (OpenAI API usage)
- **CI/CD**: Run on PR approval and main branch
- **Environment**: Requires real API keys

## 🚨 **Troubleshooting**

### **Common Issues**

1. **"ModuleNotFoundError" in tests**
   ```bash
   # Solution: Ensure PYTHONPATH is set correctly
   docker exec -it <container> bash
   echo $PYTHONPATH  # Should be /app
   ```

2. **"OpenAI API key not found" for integration tests**
   ```bash
   # Solution: Check .env file exists and has correct key
   cat .env | grep OPENAI_API_KEY
   ```

3. **"Docker build failures"**
   ```bash
   # Solution: Clean docker cache and rebuild
   docker system prune -f
   ./run-tests.sh unit  # Will rebuild
   ```

4. **"Tests hanging or timing out"**
   ```bash
   # Solution: Check for infinite loops or network issues
   docker logs <container-name>
   ```

### **Debug Mode**

```bash
# Run with debug information
./run-tests.sh unit test_models.py --verbose --tb=long

# Access test container for debugging
docker run -it --rm -v $(pwd):/app infra-bot-test-unified bash
```

## 🎯 **Best Practices**

### **Writing Tests**

1. **Unit Tests**: Mock all external dependencies
   ```python
   @patch('services.openai_service.OpenAIService.analyze_image')
   def test_image_processing(mock_analyze):
       # Test business logic, not OpenAI API
   ```

2. **Integration Tests**: Use real implementations
   ```python
   def test_real_screenshot_processing():
       # Test with actual OpenAI API calls
       service = ImageProcessingService(real_openai_service)
   ```

3. **Test Naming**: Describe functionality, not bugs
   ```python
   # ✅ Good
   def test_timezone_conversion_accuracy():
   
   # ❌ Bad  
   def test_bug_fix_timezone_issue_123():
   ```

### **Running Tests Efficiently**

```bash
# Development workflow
./run-tests.sh unit                    # Quick feedback during development
./run-tests.sh unit test_models.py     # Test specific changes
./run-tests.sh integration             # Validate before PR

# CI/CD workflow
./run-tests.sh all                     # Comprehensive validation
```

## 📚 **Legacy Test Runners**

The following legacy test runners are maintained for compatibility but not recommended:

- `./test-dev.sh` - Legacy unit test runner
- `./test-integration.sh` - Legacy integration test runner  
- `./test.sh` - Legacy comprehensive runner

**Migration**: Use `./run-tests.sh` instead for better consistency and features.