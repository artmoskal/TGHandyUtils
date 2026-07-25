#!/bin/bash

# TGHandyUtils Test Suite
# Unified test runner with support for unit/integration tests and batching

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
TEST_TYPE=""
BATCH_SIZE=""
START_INDEX=0
SHOW_HELP=false
PYTEST_ARGS=""
PYTEST_EXTRA_ARGS=()

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        unit|integration|all)
            TEST_TYPE="$1"
            shift
            ;;
        --batch)
            BATCH_SIZE="$2"
            shift 2
            ;;
        --start)
            START_INDEX="$2"
            shift 2
            ;;
        --help|-h)
            SHOW_HELP=true
            shift
            ;;
        --)
            # Everything after -- is passed to pytest
            shift
            PYTEST_ARGS="$@"
            PYTEST_EXTRA_ARGS=("$@")
            break
            ;;
        *)
            echo -e "${RED}Error: Unknown option $1${NC}"
            SHOW_HELP=true
            shift
            ;;
    esac
done

# Function to show usage
show_usage() {
    cat << EOF
🧪 TGHandyUtils Test Suite
==========================

Usage: ./test.sh <type> [options] [-- pytest args]

Test Types:
  unit          Run unit tests only (mocked, no API calls)
  integration   Run integration tests (blocked unless ALLOW_PAID_TESTS=1)
  all           Run all tests (blocked unless ALLOW_PAID_TESTS=1)

Options:
  --batch SIZE       Run tests in batches of SIZE (default: all at once)
  --start INDEX      Start from test INDEX (default: 0, use with --batch)
  --help, -h         Show this help message
  --                 Everything after -- is passed to pytest

Basic Examples:
  ./test.sh unit                     # Run all unit tests
  ALLOW_PAID_TESTS=1 ./test.sh integration  # Run paid integration tests intentionally
  ./test.sh unit --batch 50          # Run unit tests in batches of 50
  ./test.sh unit --batch 50 --start 100  # Continue from test 100

Advanced Examples (with pytest args):
  # Run specific test file
  ./test.sh unit -- tests/unit/test_core.py
  
  # Run specific test class
  ./test.sh unit -- tests/unit/test_core.py::TestClassName
  
  # Run specific test method
  ./test.sh unit -- tests/unit/test_core.py::TestClassName::test_method_name
  
  # Run tests with verbose output and stop on first failure
  ./test.sh unit -- -xvs
  
  # Run tests matching a pattern
  ./test.sh unit -- -k "test_parsing"
  
  # Debug a specific integration test
  ALLOW_PAID_TESTS=1 ./test.sh integration -- tests/integration/test_time_parsing.py::TestClass::test_method -xvs
  
  # Show only failed tests from last run
  ./test.sh unit -- --lf
  
  # Run tests in parallel (if pytest-xdist installed)
  ./test.sh unit -- -n auto

Common pytest flags:
  -x              Stop on first failure
  -v              Verbose output
  -s              Show print statements
  -k PATTERN      Run tests matching pattern
  --lf            Run last failed tests
  --ff            Run failed tests first
  --tb=short      Shorter traceback format
  --pdb           Drop into debugger on failure

Note: Integration tests use real API keys and may incur costs.
Always run from project root. Direct pytest usage is not supported.
EOF
}

# Show help if requested or if no test type provided
if [ "$SHOW_HELP" = true ] || [ -z "$TEST_TYPE" ]; then
    show_usage
    exit $([ "$SHOW_HELP" = true ] && echo 0 || echo 1)
fi

# Validate test type
if [[ ! "$TEST_TYPE" =~ ^(unit|integration|all)$ ]]; then
    echo -e "${RED}Error: Invalid test type '$TEST_TYPE'${NC}"
    echo ""
    show_usage
    exit 1
fi

# Warning and hard opt-in for paid integration tests
if [ "$TEST_TYPE" = "integration" ] || [ "$TEST_TYPE" = "all" ]; then
    if [ "${ALLOW_PAID_TESTS:-0}" != "1" ]; then
        echo -e "${RED}❌ Paid integration tests are blocked by default.${NC}"
        echo -e "${YELLOW}   Set ALLOW_PAID_TESTS=1 when you explicitly want real provider/API spend.${NC}"
        echo -e "${YELLOW}   Use ./test.sh unit for mocked no-spend validation.${NC}"
        exit 2
    fi
    echo -e "${YELLOW}⚠️  Paid integration tests enabled by ALLOW_PAID_TESTS=1 and may incur costs.${NC}"
fi

echo "🧪 TGHandyUtils Test Suite"
echo "=========================="
echo "Test type: $TEST_TYPE"
if [ -n "$BATCH_SIZE" ]; then
    echo "Batch size: $BATCH_SIZE tests"
    echo "Starting from index: $START_INDEX"
fi
if [ -n "$PYTEST_ARGS" ]; then
    echo "Pytest args: $PYTEST_ARGS"
fi
echo ""

# Resolve the test environment file before entering infra. Compose v2.20 treats env_file as
# required, while clean tagged checkouts intentionally have no gitignored .env. Keep the checkout
# clean: use the real file when present, otherwise pass a temporary empty file and remove it on exit.
REPO_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
TEMP_TEST_ENV_FILE=""

# Compose otherwise derives the project name from the shared `infra` basename, so test runs from
# independent release checkouts can tear down each other's containers. Keep cleanup stable within
# one checkout while isolating every distinct canonical root.
CHECKOUT_BASENAME="$(printf '%s' "${REPO_ROOT##*/}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | cut -c1-24)"
CHECKOUT_FINGERPRINT="$(printf '%s' "$REPO_ROOT" | cksum | awk '{print $1}')"
COMPOSE_PROJECT_NAME="tghandy-test-${CHECKOUT_BASENAME:-repo}-${CHECKOUT_FINGERPRINT}"
export COMPOSE_PROJECT_NAME

cleanup_test_env_file() {
    if [ -n "$TEMP_TEST_ENV_FILE" ]; then
        rm -f "$TEMP_TEST_ENV_FILE"
    fi
}

trap cleanup_test_env_file EXIT

if [ -f "$REPO_ROOT/.env" ]; then
    TG_TEST_ENV_FILE="$REPO_ROOT/.env"
else
    TEMP_TEST_ENV_FILE="$(mktemp "${TMPDIR:-/tmp}/tghandy-test-env.XXXXXX")"
    TG_TEST_ENV_FILE="$TEMP_TEST_ENV_FILE"
    echo -e "${YELLOW}⚠️  No .env found; using a temporary empty test environment.${NC}"
    echo "   Hermetic tiers need no secrets; credentialed suites skip or fail by their own gate."
fi
export TG_TEST_ENV_FILE

# Change to infra directory
cd "$REPO_ROOT/infra"

# Clean up any existing test containers
echo "🧹 Cleaning up previous test runs..."
docker-compose -f docker-compose.test.yml down --remove-orphans --volumes 2>/dev/null || true

# Create test results directory
mkdir -p test-results

# Function to run tests (with or without batching)
run_tests() {
    local test_files="$1"
    local batch_label="$2"
    
    local coverage_suffix=""
    local coverage_html_dir="htmlcov"
    if [ -n "$batch_label" ]; then
        coverage_suffix="-$batch_label"
        coverage_html_dir="htmlcov-$batch_label"
    fi
    
    TEST_TARGETS="$test_files" docker-compose -f docker-compose.test.yml run --rm \
        -e RUNNING_IN_DOCKER=1 \
        -e TEST_TARGETS \
        bot-test bash -c '
            echo "Running tests..."
            args=(python -m pytest)
            if [ -n "$TEST_TARGETS" ]; then
                read -r -a targets <<< "$TEST_TARGETS"
                args+=("${targets[@]}")
            fi
            args+=(-v)
            if [ "$1" != "all" ]; then
                args+=(-m "$1")
            fi
            args+=(
                --log-cli-level=DEBUG
                --log-cli-format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
                --cov=services
                --cov=ai_workflow_engine
                --cov=ai_workflow_tools
                --cov=ai_workflow_viewer
                --cov=platforms
                --cov=database
                --cov=models
                --cov=core
                --cov-report=term-missing
                --cov-report=html:/app/test-results/"$2"
                --cov-report=xml:/app/test-results/coverage"$3".xml
                --junit-xml=/app/test-results/junit"$3".xml
            )
            shift 3
            args+=("$@")
            "${args[@]}"
        ' bash "$TEST_TYPE" "$coverage_html_dir" "$coverage_suffix" "${PYTEST_EXTRA_ARGS[@]}"
}

# Run tests with or without batching
if [ -n "$BATCH_SIZE" ]; then
    echo "📋 Collecting test files..."
    
    # Collect test files based on marker
    MARKER_FILTER=""
    if [ "$TEST_TYPE" != "all" ]; then
        MARKER_FILTER="--collect-only -m $TEST_TYPE"
    fi
    
    TEST_FILES=$(docker-compose -f docker-compose.test.yml run --rm -e RUNNING_IN_DOCKER=1 bot-test bash -c "
        cd /app && \
        python -m pytest tests packages/ai_workflow_engine/tests packages/ai_workflow_tools/tests packages/ai_workflow_viewer/tests $MARKER_FILTER -q | grep '::' | cut -d':' -f1 | sort -u
    " 2>/dev/null | grep -v "^$")
    
    # Convert to array
    IFS=$'\n' read -rd '' -a TEST_ARRAY <<<"$TEST_FILES" || true
    TOTAL_FILES=${#TEST_ARRAY[@]}
    
    echo "Found $TOTAL_FILES test files"
    echo ""
    
    # Calculate batch
    END_INDEX=$((START_INDEX + BATCH_SIZE))
    if [ $END_INDEX -gt $TOTAL_FILES ]; then
        END_INDEX=$TOTAL_FILES
    fi
    
    # Extract batch
    BATCH_FILES=()
    for ((i=START_INDEX; i<END_INDEX; i++)); do
        if [ $i -lt $TOTAL_FILES ]; then
            BATCH_FILES+=("${TEST_ARRAY[$i]}")
        fi
    done
    
    if [ ${#BATCH_FILES[@]} -eq 0 ]; then
        echo -e "${RED}❌ No tests in this batch range${NC}"
        exit 1
    fi
    
    echo "🚀 Running batch: tests $START_INDEX-$((END_INDEX-1)) of $TOTAL_FILES"
    echo "Test files in this batch:"
    printf '%s\n' "${BATCH_FILES[@]}" | sed 's/^/  - /'
    echo ""
    
    # Join test files with space
    TEST_FILES_STR="${BATCH_FILES[*]}"
    
    # Run the batch
    if run_tests "$TEST_FILES_STR" "batch-${START_INDEX}"; then
        echo ""
        echo -e "${GREEN}✅ Batch completed successfully!${NC}"
        echo ""
        echo "📊 Batch Results:"
        echo "- HTML Coverage: infra/test-results/htmlcov-batch-${START_INDEX}/index.html"
        echo "- XML Coverage: infra/test-results/coverage-batch-${START_INDEX}.xml"
        echo "- JUnit XML: infra/test-results/junit-batch-${START_INDEX}.xml"
        
        # Show next batch command if needed
        if [ $END_INDEX -lt $TOTAL_FILES ]; then
            echo ""
            echo "🔄 To run next batch:"
            echo "   ./test.sh $TEST_TYPE --batch $BATCH_SIZE --start $END_INDEX"
        else
            echo ""
            echo "🎉 All batches complete!"
        fi
        
        exit_code=0
    else
        echo ""
        echo -e "${RED}❌ Batch failed!${NC}"
        echo ""
        echo "🔄 To retry this batch:"
        echo "   ./test.sh $TEST_TYPE --batch $BATCH_SIZE --start $START_INDEX"
        exit_code=1
    fi
else
    # Run all tests at once
    echo "🚀 Starting test container..."
    echo ""
    
    # If PYTEST_ARGS contains test files/patterns, use those instead of "tests/"
    if [[ "$PYTEST_ARGS" == *"tests/"* ]] || [[ "$PYTEST_ARGS" == *"::"* ]]; then
        # User specified test files, don't add "tests/"
        TEST_TARGET=""
    else
        # No specific files: run the product suite plus package-local suites
        TEST_TARGET="tests/ packages/ai_workflow_engine/tests/ packages/ai_workflow_tools/tests/ packages/ai_workflow_viewer/tests/"
    fi
    
    if run_tests "$TEST_TARGET" ""; then
        echo ""
        echo -e "${GREEN}✅ Tests completed successfully!${NC}"
        echo ""
        echo "📊 Test Results:"
        echo "- HTML Coverage Report: infra/test-results/htmlcov/index.html"
        echo "- XML Coverage Report: infra/test-results/coverage.xml"
        echo "- JUnit XML: infra/test-results/junit.xml"
        
        # Show brief coverage summary if available
        if [ -f "test-results/coverage.xml" ]; then
            echo ""
            echo "📈 Coverage Summary:"
            grep -o 'line-rate="[^"]*"' test-results/coverage.xml | head -1 | sed 's/line-rate="//g' | sed 's/"//g' | awk '{printf "Line Coverage: %.1f%%\n", $1*100}'
        fi
        
        exit_code=0
    else
        echo ""
        echo -e "${RED}❌ Tests failed!${NC}"
        echo ""
        echo "💡 Tip: If tests are timing out, try running in batches:"
        echo "   ./test.sh $TEST_TYPE --batch 50"
        exit_code=1
    fi
fi

# Cleanup
echo ""
echo "🧹 Cleaning up test containers..."
docker-compose -f docker-compose.test.yml down --remove-orphans --volumes 2>/dev/null || true

exit $exit_code
