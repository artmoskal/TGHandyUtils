#!/bin/bash

# Quick development test runner
# Usage: ./test-dev.sh [unit|integration|fast|all] [--verbose]

set -e

# Default test type
TEST_TYPE=${1:-"unit"}
VERBOSE_FLAG=""

# Check for verbose flag
if [[ "$2" == "--verbose" || "$1" == "--verbose" ]]; then
    VERBOSE_FLAG="-v"
fi

echo "🧪 TGHandyUtils Quick Test: $TEST_TYPE"
echo "=================================="

cd "$(dirname "$0")/infra"

# Clean up any existing test containers
docker-compose -f docker-compose.test.yml down --remove-orphans --volumes 2>/dev/null || true

# Create test results directory
mkdir -p test-results

# Build test command based on type
case $TEST_TYPE in
    "unit")
        TEST_PATH="tests/unit/"
        echo "🎯 Running unit tests only..."
        ;;
    "integration") 
        TEST_PATH="tests/integration/"
        echo "🔄 Running integration tests only..."
        ;;
    "fast")
        TEST_PATH="tests/ -m 'not slow'"
        echo "⚡ Running fast tests only..."
        ;;
    "all")
        TEST_PATH="tests/"
        echo "🚀 Running all tests..."
        ;;
    *)
        TEST_PATH="$TEST_TYPE"
        echo "🎯 Running custom test path: $TEST_TYPE"
        ;;
esac

# Create temporary docker compose for specific test
cat > docker-compose.test-dev.yml << EOF
version: '3.8'

services:
  bot-test-dev:
    build:
      context: ../
      dockerfile: infra/Dockerfile
    environment:
      - TELEGRAM_BOT_TOKEN=test_token_not_used
      - OPENAI_API_KEY=test_key_not_used
      - PYTHONPATH=/app
    volumes:
      - ../:/app
      - ./test-results:/app/test-results
      - ../.env:/app/.env
    working_dir: /app
    command: >
      bash -c "
        echo 'Installing test dependencies...' &&
        mamba install -y pytest pytest-asyncio pytest-mock pytest-cov coverage -c conda-forge &&
        pip install aioresponses factory-boy python-dotenv &&
        echo 'Running $TEST_TYPE tests...' &&
        python -m pytest $TEST_PATH $VERBOSE_FLAG --cov=services --cov=platforms --cov=database --cov=models --cov=core --cov-report=term-missing
      "
    networks:
      - test-network

networks:
  test-network:
    driver: bridge
EOF

echo ""
if docker-compose -f docker-compose.test-dev.yml up --build --abort-on-container-exit; then
    echo ""
    echo "✅ $TEST_TYPE tests completed successfully!"
    exit_code=0
else
    echo ""
    echo "❌ $TEST_TYPE tests failed!"
    exit_code=1
fi

# Cleanup
echo ""
echo "🧹 Cleaning up..."
docker-compose -f docker-compose.test-dev.yml down --remove-orphans --volumes 2>/dev/null || true
rm -f docker-compose.test-dev.yml

exit $exit_code