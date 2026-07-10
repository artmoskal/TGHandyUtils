## Testing Rules - MUST FOLLOW
- **ALWAYS use ./test.sh from project root** - NO EXCEPTIONS (test_batch.sh / test_all_batches.sh do NOT exist)
- **NEVER use direct pytest commands**
- **NEVER use docker-compose test commands directly**
- **NEVER use python -m pytest**
- For large test suites that timeout, use batch testing:
  - `./test.sh unit --batch 50` - Run unit tests in batches of 50
  - `./test.sh unit --batch 50 --start 100` - Continue from test 100
- If you need to run specific tests, use `./test.sh unit -- --cov-fail-under=0 -q <path>` or ask user first

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
