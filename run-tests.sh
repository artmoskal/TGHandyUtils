#!/bin/bash

# 🧪 TGHandyUtils Unified Test Runner
# 
# Usage:
#   ./run-tests.sh unit                           # Run all unit tests
#   ./run-tests.sh integration                    # Run all integration tests  
#   ./run-tests.sh all                           # Run both unit and integration
#   ./run-tests.sh unit --verbose                # Run unit tests with verbose output
#   ./run-tests.sh integration test_screenshot.py  # Run specific integration test file
#   ./run-tests.sh unit "test_models.py::TestTaskModels::test_task_create"  # Run specific test
#
# Features:
# - Same Docker environment for both test types (production-like)
# - Unified parameterization system
# - Coverage reporting for all test types
# - Environment variable handling (mock vs real APIs)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default values
TEST_TYPE=""
TEST_PATH=""
EXTRA_ARGS=""
VERBOSE_FLAG=""

# Help function
show_help() {
    echo -e "${BLUE}🧪 TGHandyUtils Unified Test Runner${NC}"
    echo ""
    echo "Usage: $0 <test-type> [test-path] [pytest-args]"
    echo ""
    echo "Test Types:"
    echo "  unit          Run unit tests (mocked dependencies)"
    echo "  integration   Run integration tests (real APIs, requires .env)"
    echo "  all           Run both unit and integration tests"
    echo ""
    echo "Examples:"
    echo "  $0 unit                                    # All unit tests"
    echo "  $0 integration                             # All integration tests"
    echo "  $0 unit test_models.py                     # Specific unit test file"
    echo "  $0 integration test_screenshot.py          # Specific integration test"
    echo "  $0 unit \"test_models.py::TestTask\"         # Specific test class"
    echo "  $0 integration test_parsing.py -k midnight # Tests matching 'midnight'"
    echo "  $0 all --verbose                           # All tests with verbose output"
    echo ""
    echo "Environment:"
    echo "  Unit tests:        Use mock tokens (no API costs)"
    echo "  Integration tests: Require real .env file with API keys"
    echo ""
}

# Parse arguments
if [ $# -eq 0 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    show_help
    exit 0
fi

TEST_TYPE=$1
shift

# Parse remaining arguments
while [ $# -gt 0 ]; do
    case $1 in
        --verbose|-v)
            VERBOSE_FLAG="-v"
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        -k|--tb|--cov*|--junit*|--html*)
            # Pytest arguments
            EXTRA_ARGS="$EXTRA_ARGS $1"
            if [ $# -gt 1 ] && [[ ! "$2" =~ ^- ]]; then
                shift
                EXTRA_ARGS="$EXTRA_ARGS $1"
            fi
            shift
            ;;
        *)
            if [ -z "$TEST_PATH" ]; then
                TEST_PATH="$1"
            else
                EXTRA_ARGS="$EXTRA_ARGS $1"
            fi
            shift
            ;;
    esac
done

# Validate test type
case $TEST_TYPE in
    unit|integration|all)
        ;;
    *)
        echo -e "${RED}❌ Error: Invalid test type '$TEST_TYPE'${NC}"
        echo "Valid types: unit, integration, all"
        exit 1
        ;;
esac

echo -e "${BLUE}🧪 TGHandyUtils Unified Test Runner${NC}"
echo "========================================"
echo ""

# Function to run tests
run_test_type() {
    local type=$1
    local test_path=$2
    local args="$3"
    
    echo -e "${YELLOW}🚀 Running $type tests...${NC}"
    
    # Determine test directory and environment
    if [ "$type" = "unit" ]; then
        local base_path="tests/unit/"
        local env_type="mock"
        local api_key_check=""
    else
        local base_path="tests/integration/"
        local env_type="real"
        
        # Check for .env file for integration tests
        if [ ! -f .env ]; then
            echo -e "${RED}❌ Error: .env file required for integration tests${NC}"
            echo "Create .env with OPENAI_API_KEY=your-key"
            return 1
        fi
        
        if ! grep -q "OPENAI_API_KEY=" .env; then
            echo -e "${RED}❌ Error: OPENAI_API_KEY not found in .env${NC}"
            return 1
        fi
        
        echo -e "${YELLOW}⚠️  WARNING: Integration tests consume OpenAI API tokens!${NC}"
    fi
    
    # Build full test path
    if [ -n "$test_path" ]; then
        if [[ "$test_path" == tests/* ]]; then
            local full_path="$test_path"
        else
            local full_path="$base_path$test_path"
        fi
    else
        local full_path="$base_path"
    fi
    
    echo "Test configuration:"
    echo "  Type: $type ($env_type environment)"
    echo "  Path: $full_path"
    echo "  Args: $VERBOSE_FLAG $args"
    echo ""
    
    cd "$(dirname "$0")/infra"
    
    # Create unified docker-compose config
    cat > docker-compose.test-unified.yml << EOF
version: '3.8'

services:
  bot-test-unified:
    build:
      context: ../
      dockerfile: infra/Dockerfile
    environment:
      - PYTHONPATH=/app
      - TEST_TYPE=$type
      # Environment variables based on test type
$(if [ "$type" = "unit" ]; then
cat << 'UNIT_ENV'
      - TELEGRAM_BOT_TOKEN=test_token_not_used
      - OPENAI_API_KEY=test_key_not_used
UNIT_ENV
else
cat << 'INTEGRATION_ENV'
    env_file:
      - ../.env  # Load real API keys for integration tests
INTEGRATION_ENV
fi)
    volumes:
      - ../:/app
      - ./test-results:/app/test-results
    working_dir: /app
    command: >
      bash -c "
        echo '=== Running $type tests ===' &&
        python -m pytest '$full_path' $VERBOSE_FLAG $args --cov=services --cov=platforms --cov=database --cov=models --cov=core --cov-report=term-missing --cov-report=html:/app/test-results/htmlcov-$type --cov-report=xml:/app/test-results/coverage-$type.xml --junit-xml=/app/test-results/junit-$type.xml
      "
    networks:
      - test-network

networks:
  test-network:
    driver: bridge
EOF
    
    # Run the tests
    local success=true
    if ! docker-compose -f docker-compose.test-unified.yml up --build --abort-on-container-exit; then
        success=false
    fi
    
    # Cleanup
    docker-compose -f docker-compose.test-unified.yml down --remove-orphans --volumes 2>/dev/null || true
    rm -f docker-compose.test-unified.yml
    cd ..
    
    if [ "$success" = "true" ]; then
        echo -e "${GREEN}✅ $type tests completed successfully!${NC}"
        return 0
    else
        echo -e "${RED}❌ $type tests failed!${NC}"
        return 1
    fi
}

# Execute tests based on type
overall_success=true

case $TEST_TYPE in
    unit)
        if ! run_test_type "unit" "$TEST_PATH" "$EXTRA_ARGS"; then
            overall_success=false
        fi
        ;;
    integration)
        if ! run_test_type "integration" "$TEST_PATH" "$EXTRA_ARGS"; then
            overall_success=false
        fi
        ;;
    all)
        echo -e "${BLUE}Running comprehensive test suite...${NC}"
        echo ""
        
        if ! run_test_type "unit" "" "$EXTRA_ARGS"; then
            overall_success=false
        fi
        
        echo ""
        echo -e "${BLUE}Proceeding to integration tests...${NC}"
        echo ""
        
        if ! run_test_type "integration" "" "$EXTRA_ARGS"; then
            overall_success=false
        fi
        ;;
esac

# Final summary
echo ""
echo "========================================"
if [ "$overall_success" = "true" ]; then
    echo -e "${GREEN}🎉 All tests completed successfully!${NC}"
    echo ""
    echo "📊 Test Results Available:"
    echo "  HTML Coverage: infra/test-results/htmlcov-*/index.html"
    echo "  XML Coverage:  infra/test-results/coverage-*.xml"
    echo "  JUnit XML:     infra/test-results/junit-*.xml"
    exit 0
else
    echo -e "${RED}💥 Some tests failed!${NC}"
    exit 1
fi