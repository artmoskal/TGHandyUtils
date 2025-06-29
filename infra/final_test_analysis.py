import os
import re
from pathlib import Path

def analyze_test_issues():
    """Analyze potential issues with test discovery."""
    
    print("=== Comprehensive Test Analysis ===\n")
    
    # 1. Count all tests
    total_tests = 0
    all_test_files = []
    
    for pattern in ['**/test_*.py', '**/*_test.py']:
        files = list(Path('.').glob(pattern))
        for f in files:
            if f not in all_test_files:
                all_test_files.append(f)
    
    print(f"📊 Found {len(all_test_files)} test files")
    
    # 2. Analyze test discovery based on pytest.ini
    pytest_discoverable = []
    root_tests = []
    
    for test_file in all_test_files:
        with open(test_file, 'r') as f:
            content = f.read()
            
        # Count test methods
        test_count = len(re.findall(r'(?:^|\n)\s*(?:async\s+)?def\s+test_\w+', content))
        
        if str(test_file).startswith('tests/'):
            pytest_discoverable.append((test_file, test_count))
            total_tests += test_count
        else:
            root_tests.append((test_file, test_count))
            total_tests += test_count
    
    print(f"🎯 Total test methods found: {total_tests}")
    
    print("\n=== Tests discoverable by pytest (in tests/ directory) ===")
    discoverable_count = 0
    for test_file, count in pytest_discoverable:
        print(f"  ✅ {test_file}: {count} tests")
        discoverable_count += count
    
    print(f"\n📈 Discoverable tests: {discoverable_count}")
    
    print("\n=== Tests NOT discoverable by pytest (in root directory) ===")
    not_discoverable_count = 0
    for test_file, count in root_tests:
        print(f"  ❌ {test_file}: {count} tests")
        not_discoverable_count += count
    
    print(f"\n📉 Non-discoverable tests: {not_discoverable_count}")
    
    # 3. Check for potential issues
    print("\n=== Potential Issues ===")
    
    issues_found = False
    
    # Check for empty test files
    for test_file in all_test_files:
        with open(test_file, 'r') as f:
            content = f.read()
        
        if not re.search(r'(?:^|\n)\s*(?:async\s+)?def\s+test_\w+', content):
            print(f"  ⚠️  {test_file}: No test methods found")
            issues_found = True
    
    # Check for import errors (basic check)
    problematic_imports = []
    for test_file in all_test_files:
        with open(test_file, 'r') as f:
            content = f.read()
        
        # Look for imports that might cause issues
        if 'from services.partner_service' in content:
            problematic_imports.append((test_file, 'partner_service'))
        if 'from services.task_service' in content:
            problematic_imports.append((test_file, 'task_service'))
        if 'from models.partner' in content:
            problematic_imports.append((test_file, 'models.partner'))
        if 'from models.user' in content:
            problematic_imports.append((test_file, 'models.user'))
    
    if problematic_imports:
        print(f"  ⚠️  Files with potentially problematic imports:")
        for test_file, import_name in problematic_imports:
            print(f"     - {test_file}: imports {import_name}")
        issues_found = True
    
    if not issues_found:
        print("  ✅ No obvious issues found")
    
    # 4. Summary
    print(f"\n=== SUMMARY ===")
    print(f"🔍 Total test files: {len(all_test_files)}")
    print(f"🎯 Total test methods: {total_tests}")
    print(f"✅ Discoverable by pytest: {discoverable_count} tests")
    print(f"❌ NOT discoverable by pytest: {not_discoverable_count} tests")
    
    if not_discoverable_count > 0:
        print(f"\n💡 RECOMMENDATION:")
        print(f"   Move test files from root to tests/ directory OR")
        print(f"   Update pytest.ini testpaths to include root directory")
        print(f"   Currently: testpaths = tests")
        print(f"   Could be: testpaths = tests .")
    
    # Show the discrepancy
    if discoverable_count < 50:  # Assuming 15 was reported as too few
        print(f"\n🚨 LIKELY CAUSE OF LOW TEST COUNT:")
        print(f"   If only 15 tests are running, there may be:")
        print(f"   - Import errors preventing test collection")
        print(f"   - Missing dependencies")
        print(f"   - Syntax errors in test files")
        print(f"   - Test methods not following naming conventions")

if __name__ == '__main__':
    analyze_test_issues()

