import subprocess
import sys
import re
from pathlib import Path

def run_pytest_collect():
    """Run pytest collection and analyze output."""
    print("=== Running pytest collection analysis ===\n")
    
    # Try different pytest collection commands
    commands = [
        ["python", "-m", "pytest", "--collect-only", "-q"],
        ["python", "-m", "pytest", "--collect-only", "tests/", "-q"],
        ["python", "-m", "pytest", "--collect-only", "tests/unit/", "-q"],
        ["python", "-m", "pytest", "--collect-only", "tests/integration/", "-q"],
    ]
    
    for cmd in commands:
        print(f"\n📍 Running: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True,
                cwd="/Users/artemm/PycharmProjects/TGHandyUtils"
            )
            
            if result.returncode == 0:
                # Count collected tests
                lines = result.stdout.strip().split('\n')
                test_count = 0
                for line in lines:
                    if '::' in line and 'test_' in line:
                        test_count += 1
                print(f"✅ Collected {test_count} tests")
                
                # Show first few test names
                test_lines = [l for l in lines if '::' in l and 'test_' in l]
                if test_lines:
                    print("   Sample tests:")
                    for line in test_lines[:5]:
                        print(f"   - {line}")
                    if len(test_lines) > 5:
                        print(f"   ... and {len(test_lines) - 5} more")
            else:
                print(f"❌ Error: {result.stderr}")
        except Exception as e:
            print(f"❌ Failed to run command: {e}")

def check_pytest_config():
    """Check pytest configuration."""
    print("\n=== Checking pytest configuration ===")
    
    config_files = ['pytest.ini', 'pyproject.toml', 'tox.ini', 'setup.cfg']
    
    for config_file in config_files:
        path = Path(f"/Users/artemm/PycharmProjects/TGHandyUtils/{config_file}")
        if path.exists():
            print(f"\n📄 Found {config_file}")
            with open(path, 'r') as f:
                content = f.read()
                if 'testpaths' in content:
                    for line in content.split('\n'):
                        if 'testpaths' in line:
                            print(f"   ⚙️  {line.strip()}")

def check_conftest_files():
    """Check for conftest.py files that might affect test discovery."""
    print("\n=== Checking conftest.py files ===")
    
    conftest_files = list(Path("/Users/artemm/PycharmProjects/TGHandyUtils").glob("**/conftest.py"))
    
    for conftest in conftest_files:
        print(f"\n📄 {conftest}")
        with open(conftest, 'r') as f:
            content = f.read()
            # Check for pytest_collection_modifyitems or other hooks
            if 'pytest_collection_modifyitems' in content:
                print("   ⚠️  Contains pytest_collection_modifyitems hook")
            if 'pytest_ignore_collect' in content:
                print("   ⚠️  Contains pytest_ignore_collect hook")
            if 'deselect' in content.lower():
                print("   ⚠️  May contain test deselection logic")

if __name__ == '__main__':
    check_pytest_config()
    check_conftest_files()
    # Note: Skipping actual pytest run as it's not installed in this environment

