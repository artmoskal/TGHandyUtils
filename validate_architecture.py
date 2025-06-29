#!/usr/bin/env python3
"""
Architecture validation script.
Ensures all architectural decisions are properly implemented.
"""

import sys
import ast
import inspect
from pathlib import Path
from typing import List, Dict, Set

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent))


class ArchitectureValidator:
    """Validates architectural compliance."""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.project_root = Path(__file__).parent
    
    def log_error(self, message: str):
        """Log an architecture error."""
        self.errors.append(f"❌ {message}")
        print(f"❌ {message}")
    
    def log_warning(self, message: str):
        """Log an architecture warning."""
        self.warnings.append(f"⚠️  {message}")
        print(f"⚠️  {message}")
    
    def log_success(self, message: str):
        """Log a successful check."""
        print(f"✅ {message}")
    
    def validate_no_legacy_imports(self):
        """Ensure no legacy imports remain."""
        print("\n🔍 Validating no legacy imports...")
        
        legacy_patterns = [
            'from models.partner',
            'from models.user',
            'import partner',
            'import user',
            'IPartnerService',
            'IUserRepository', 
            'PartnerService',
            'UserRepository',
            'partner_service',
            'user_repo'
        ]
        
        python_files = list(self.project_root.rglob("*.py"))
        python_files = [f for f in python_files if 'test' not in str(f) and '__pycache__' not in str(f)]
        
        legacy_found = False
        for file_path in python_files:
            try:
                content = file_path.read_text()
                for pattern in legacy_patterns:
                    if pattern in content:
                        self.log_error(f"Legacy import '{pattern}' found in {file_path}")
                        legacy_found = True
            except Exception as e:
                self.log_warning(f"Could not read {file_path}: {e}")
        
        if not legacy_found:
            self.log_success("No legacy imports found")
    
    def validate_dependency_injection(self):
        """Validate dependency injection patterns."""
        print("\n🔍 Validating dependency injection...")
        
        try:
            # Check container structure
            from core.container import ApplicationContainer, container
            from core.recipient_container import RecipientContainer, recipient_container
            
            # Verify containers can be instantiated
            test_container = ApplicationContainer()
            test_recipient_container = RecipientContainer()
            
            self.log_success("DI containers can be instantiated")
            
            # Check service locators
            from core.recipient_container import get_recipient_service, get_recipient_task_service
            self.log_success("Service locators are available")
            
        except Exception as e:
            self.log_error(f"DI validation failed: {e}")
    
    def validate_interface_compliance(self):
        """Validate that implementations match their interfaces."""
        print("\n🔍 Validating interface compliance...")
        
        try:
            # Check recipient interfaces
            from core.recipient_interfaces import (
                IRecipientService, IUserPlatformRepository, 
                ISharedRecipientRepository, IUserPreferencesV2Repository
            )
            from services.recipient_service import RecipientService
            from database.recipient_repositories import (
                UserPlatformRepository, SharedRecipientRepository, UserPreferencesV2Repository
            )
            
            # Verify inheritance
            assert issubclass(RecipientService, IRecipientService), "RecipientService doesn't implement IRecipientService"
            assert issubclass(UserPlatformRepository, IUserPlatformRepository), "UserPlatformRepository doesn't implement interface"
            assert issubclass(SharedRecipientRepository, ISharedRecipientRepository), "SharedRecipientRepository doesn't implement interface"
            assert issubclass(UserPreferencesV2Repository, IUserPreferencesV2Repository), "UserPreferencesV2Repository doesn't implement interface"
            
            self.log_success("All implementations match their interfaces")
            
        except Exception as e:
            self.log_error(f"Interface compliance validation failed: {e}")
    
    def validate_model_consistency(self):
        """Validate model definitions and consistency."""
        print("\n🔍 Validating model consistency...")
        
        try:
            from models.recipient import (
                UserPlatform, UserPlatformCreate, UserPlatformUpdate,
                SharedRecipient, SharedRecipientCreate, SharedRecipientUpdate,
                Recipient, UserPreferencesV2, UserPreferencesV2Create, UserPreferencesV2Update
            )
            from models.task import TaskCreate, TaskDB, PlatformTaskData
            
            # Test model instantiation
            recipient = Recipient(
                id="test_1",
                name="Test",
                platform_type="todoist",
                type="user_platform",
                enabled=True
            )
            
            task = TaskCreate(
                title="Test",
                description="Test",
                due_time="2024-01-01T12:00:00Z"
            )
            
            platform_data = PlatformTaskData(
                title="Test",
                description="Test", 
                due_time="2024-01-01T12:00:00Z"
            )
            
            self.log_success("All models can be instantiated")
            
        except Exception as e:
            self.log_error(f"Model validation failed: {e}")
    
    def validate_service_architecture(self):
        """Validate service layer architecture."""
        print("\n🔍 Validating service architecture...")
        
        try:
            from services.recipient_service import RecipientService
            from services.recipient_task_service import RecipientTaskService
            
            # Check that services have proper constructor signatures
            recipient_sig = inspect.signature(RecipientService.__init__)
            expected_params = {'self', 'platform_repo', 'shared_repo', 'prefs_repo'}
            actual_params = set(recipient_sig.parameters.keys())
            
            if expected_params != actual_params:
                self.log_error(f"RecipientService constructor mismatch. Expected: {expected_params}, Got: {actual_params}")
            else:
                self.log_success("RecipientService constructor is correct")
            
            task_sig = inspect.signature(RecipientTaskService.__init__)
            expected_task_params = {'self', 'task_repo', 'recipient_service'}
            actual_task_params = set(task_sig.parameters.keys())
            
            if expected_task_params != actual_task_params:
                self.log_error(f"RecipientTaskService constructor mismatch. Expected: {expected_task_params}, Got: {actual_task_params}")
            else:
                self.log_success("RecipientTaskService constructor is correct")
                
        except Exception as e:
            self.log_error(f"Service architecture validation failed: {e}")
    
    def validate_repository_pattern(self):
        """Validate repository pattern implementation."""
        print("\n🔍 Validating repository pattern...")
        
        try:
            from database.recipient_repositories import (
                UserPlatformRepository, SharedRecipientRepository, UserPreferencesV2Repository
            )
            from database.repositories import TaskRepository
            
            # Check that all repositories have database manager
            repos = [UserPlatformRepository, SharedRecipientRepository, UserPreferencesV2Repository, TaskRepository]
            
            for repo_class in repos:
                repo_sig = inspect.signature(repo_class.__init__)
                if 'db_manager' not in repo_sig.parameters:
                    self.log_error(f"{repo_class.__name__} missing db_manager parameter")
                else:
                    self.log_success(f"{repo_class.__name__} has proper constructor")
                    
        except Exception as e:
            self.log_error(f"Repository pattern validation failed: {e}")
    
    def validate_handler_architecture(self):
        """Validate handler architecture."""
        print("\n🔍 Validating handler architecture...")
        
        try:
            # Check that handlers import only from recipient system
            handlers_file = self.project_root / "handlers.py"
            if handlers_file.exists():
                content = handlers_file.read_text()
                
                # Should have recipient imports
                if "from core.recipient_container import" not in content:
                    self.log_error("Handlers not using recipient container")
                else:
                    self.log_success("Handlers use recipient container")
                
                # Should have recipient states
                if "from states.recipient_states import" not in content:
                    self.log_error("Handlers not using recipient states")
                else:
                    self.log_success("Handlers use recipient states")
                
                # Should have recipient keyboards
                if "from keyboards.recipient import" not in content:
                    self.log_error("Handlers not using recipient keyboards")
                else:
                    self.log_success("Handlers use recipient keyboards")
            else:
                self.log_error("handlers.py not found")
                
        except Exception as e:
            self.log_error(f"Handler architecture validation failed: {e}")
    
    def validate_test_coverage(self):
        """Validate test coverage and structure."""
        print("\n🔍 Validating test coverage...")
        
        test_files = [
            "tests/unit/test_recipient_service.py",
            "tests/unit/test_recipient_task_service.py",
            "tests/conftest.py"
        ]
        
        missing_tests = []
        for test_file in test_files:
            test_path = self.project_root / test_file
            if not test_path.exists():
                missing_tests.append(test_file)
        
        if missing_tests:
            for missing in missing_tests:
                self.log_error(f"Missing test file: {missing}")
        else:
            self.log_success("All expected test files exist")
        
        # Check for legacy test files
        legacy_test_patterns = ["test_task_service.py", "test_partner", "test_user"]
        test_dir = self.project_root / "tests"
        
        if test_dir.exists():
            for test_file in test_dir.rglob("*.py"):
                for pattern in legacy_test_patterns:
                    if pattern in test_file.name:
                        self.log_error(f"Legacy test file found: {test_file}")
    
    def validate_database_schema(self):
        """Validate database schema consistency."""
        print("\n🔍 Validating database schema...")
        
        try:
            from database.recipient_schema import create_recipient_tables
            import tempfile
            import sqlite3
            
            # Test schema creation
            with tempfile.NamedTemporaryFile(suffix='.db') as tmp_db:
                conn = sqlite3.connect(tmp_db.name)
                
                try:
                    create_recipient_tables(conn)
                    
                    # Check that tables were created
                    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [row[0] for row in cursor.fetchall()]
                    
                    expected_tables = ['user_platforms', 'shared_recipients', 'user_preferences_v2']
                    missing_tables = [t for t in expected_tables if t not in tables]
                    
                    if missing_tables:
                        self.log_error(f"Missing database tables: {missing_tables}")
                    else:
                        self.log_success("All expected database tables created")
                        
                finally:
                    conn.close()
                    
        except Exception as e:
            self.log_error(f"Database schema validation failed: {e}")
    
    def validate_file_organization(self):
        """Validate file organization and structure."""
        print("\n🔍 Validating file organization...")
        
        expected_files = [
            "core/recipient_container.py",
            "core/recipient_interfaces.py", 
            "database/recipient_repositories.py",
            "database/recipient_schema.py",
            "services/recipient_service.py",
            "services/recipient_task_service.py",
            "models/recipient.py",
            "keyboards/recipient.py",
            "states/recipient_states.py"
        ]
        
        missing_files = []
        for file_path in expected_files:
            full_path = self.project_root / file_path
            if not full_path.exists():
                missing_files.append(file_path)
        
        if missing_files:
            for missing in missing_files:
                self.log_error(f"Missing expected file: {missing}")
        else:
            self.log_success("All expected files are present")
        
        # Check for legacy files that should be removed
        legacy_files = [
            "models/partner.py",
            "models/user.py", 
            "services/partner_service.py",
            "keyboards/inline.py",
            "states/platform_states.py"
        ]
        
        found_legacy = []
        for file_path in legacy_files:
            full_path = self.project_root / file_path
            if full_path.exists():
                found_legacy.append(file_path)
        
        if found_legacy:
            for legacy in found_legacy:
                self.log_error(f"Legacy file still exists: {legacy}")
        else:
            self.log_success("No legacy files found")
    
    def run_all_validations(self):
        """Run all architecture validations."""
        print("🏗️  Starting comprehensive architecture validation...")
        
        validations = [
            self.validate_no_legacy_imports,
            self.validate_dependency_injection,
            self.validate_interface_compliance,
            self.validate_model_consistency,
            self.validate_service_architecture,
            self.validate_repository_pattern,
            self.validate_handler_architecture,
            self.validate_test_coverage,
            self.validate_database_schema,
            self.validate_file_organization
        ]
        
        for validation in validations:
            try:
                validation()
            except Exception as e:
                self.log_error(f"Validation {validation.__name__} failed with exception: {e}")
        
        # Summary
        print("\n📊 ARCHITECTURE VALIDATION SUMMARY")
        print(f"Errors: {len(self.errors)}")
        print(f"Warnings: {len(self.warnings)}")
        
        if self.errors:
            print("\n❌ ARCHITECTURE ERRORS:")
            for error in self.errors:
                print(f"  {error}")
        
        if self.warnings:
            print("\n⚠️  ARCHITECTURE WARNINGS:")
            for warning in self.warnings:
                print(f"  {warning}")
        
        if not self.errors:
            print("\n🎉 ARCHITECTURE VALIDATION PASSED!")
            return True
        else:
            print(f"\n💥 ARCHITECTURE VALIDATION FAILED with {len(self.errors)} errors")
            return False


def main():
    """Main validation runner."""
    validator = ArchitectureValidator()
    success = validator.run_all_validations()
    
    if success:
        print("\n✅ Architecture validation completed successfully")
        sys.exit(0)
    else:
        print("\n❌ Architecture validation failed")
        sys.exit(1)


if __name__ == "__main__":
    main()