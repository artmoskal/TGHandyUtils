#!/bin/bash

# Check specific failing tests
echo "=== Checking Recipient Task Service Validation ==="
./run-single-test.sh "tests/unit/test_recipient_task_service.py::TestRecipientTaskService::test_validate_task_data_empty_title"

echo -e "\n=== Checking Database Manager Creation ==="
./run-single-test.sh "tests/unit/test_repositories.py::TestDatabaseConnection::test_database_manager_creation"

echo -e "\n=== Checking Todoist Create Task Success ==="
./run-single-test.sh "tests/unit/test_platforms.py::TestTodoistPlatform::test_create_task_success"

echo -e "\n=== Running All Tests Summary ==="
./test-dev.sh unit | tail -5