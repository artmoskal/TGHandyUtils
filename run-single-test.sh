#!/bin/bash

# Quick test runner for single tests
# Usage: ./run-single-test.sh "test_path::test_name"

if [ -z "$1" ]; then
    echo "Usage: ./run-single-test.sh 'test_path::test_name'"
    echo "Example: ./run-single-test.sh 'tests/unit/test_models.py::TestTaskModels::test_task_db_model'"
    exit 1
fi

TEST_PATH="$1"

docker run --rm -v $(pwd):/app infra-bot-test-dev python -m pytest "$TEST_PATH" -v