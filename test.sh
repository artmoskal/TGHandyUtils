#!/bin/bash

# TGHandyUtils Docker Test Runner
# Run tests in containerized environment

set -e

echo "🧪 TGHandyUtils Test Suite"
echo "=========================="

# Change to infra directory
cd "$(dirname "$0")/infra"

# Clean up any existing test containers
echo "🧹 Cleaning up previous test runs..."
docker-compose -f docker-compose.test.yml down --remove-orphans --volumes 2>/dev/null || true

# Create test results directory
mkdir -p test-results

# Run tests in container
echo "🚀 Starting test container..."
echo ""

if docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit; then
    echo ""
    echo "✅ Tests completed successfully!"
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
    echo "❌ Tests failed!"
    exit_code=1
fi

# Cleanup
echo ""
echo "🧹 Cleaning up test containers..."
docker-compose -f docker-compose.test.yml down --remove-orphans --volumes 2>/dev/null || true

exit $exit_code