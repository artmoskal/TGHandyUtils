#!/bin/bash
# Universal integration test runner
# Usage: ./test-integration.sh [test-path] [pytest-args]
# Examples:
#   ./test-integration.sh                                    # Run all integration tests
#   ./test-integration.sh test_scheduling_validation.py      # Run specific test file
#   ./test-integration.sh test_scheduling_validation.py -k "midnight"  # Run specific test

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🧪 TGHandyUtils Integration Test Runner${NC}"
echo "=========================================="

# Check for .env file
if [ ! -f .env ]; then
    echo -e "${RED}❌ Error: .env file not found!${NC}"
    echo "Integration tests require real API keys."
    echo "Please create a .env file with:"
    echo "  OPENAI_API_KEY=your-api-key"
    echo "  TELEGRAM_BOT_TOKEN=your-bot-token"
    exit 1
fi

# Check if OPENAI_API_KEY is set in .env
if ! grep -q "OPENAI_API_KEY=" .env; then
    echo -e "${RED}❌ Error: OPENAI_API_KEY not found in .env!${NC}"
    echo "Integration tests require a real OpenAI API key."
    exit 1
fi

# Parse arguments
TEST_PATH=${1:-""}
PYTEST_ARGS=${2:-"-v"}

# Build full test path if specific file provided
if [ -n "$TEST_PATH" ] && [[ ! "$TEST_PATH" == /* ]]; then
    # If it's just a filename, prepend the integration test directory
    if [[ ! "$TEST_PATH" == tests/* ]]; then
        TEST_PATH="tests/integration/$TEST_PATH"
    fi
fi

echo -e "${YELLOW}⚠️  WARNING: Integration tests consume OpenAI API tokens!${NC}"
echo ""
echo "Test configuration:"
echo "  Test path: ${TEST_PATH:-tests/integration/}"
echo "  Pytest args: $PYTEST_ARGS"
echo ""

cd infra

# Export environment variables for docker-compose
export TEST_PATH
export PYTEST_ARGS

# Run tests
if docker-compose -f docker-compose.test-integration.yml up --build --abort-on-container-exit; then
    echo -e "\n${GREEN}✅ Integration tests completed successfully!${NC}"
    exit_code=0
else
    echo -e "\n${RED}❌ Integration tests failed!${NC}"
    exit_code=1
fi

# Cleanup
echo -e "\n🧹 Cleaning up..."
docker-compose -f docker-compose.test-integration.yml down --remove-orphans --volumes 2>/dev/null || true

cd ..
exit $exit_code