## Development Guidelines
- Never change extension code locally, it should be done on 9881 and then tested, when working pushed! Do not push from local machine!

## Testing Rules - MUST FOLLOW
- **ALWAYS use ./test.sh, ./test_batch.sh, or ./test_all_batches.sh from project root** - NO EXCEPTIONS
- **NEVER use direct pytest commands**
- **NEVER use docker-compose test commands directly**
- **NEVER use python -m pytest**
- For large test suites that timeout, use batch testing:
  - `./test_batch.sh 50 0` - Run first 50 tests
  - `./test_all_batches.sh` - Run all tests in batches automatically
- If you need to run specific tests, use the batch scripts or ask user first

## Why These Rules Exist
- test.sh handles all container setup/cleanup properly
- Direct pytest won't have proper environment
- Docker commands without test.sh wrapper miss configuration
- Consistency prevents "random shit" behavior

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.