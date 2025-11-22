"""
Validation script to check if the backend is properly set up
Run this before starting the server to ensure everything is configured correctly
"""
import os
import sys
from pathlib import Path


def check_python_version():
    """Check Python version"""
    print("🐍 Checking Python version...")
    version = sys.version_info
    if version.major == 3 and version.minor >= 9:
        print(f"   ✅ Python {version.major}.{version.minor}.{version.micro}")
        return True
    else:
        print(f"   ❌ Python {version.major}.{version.minor}.{version.micro} (Need 3.9+)")
        return False


def check_dependencies():
    """Check if required packages are installed"""
    print("\n📦 Checking dependencies...")
    required = [
        'fastapi',
        'uvicorn',
        'pydantic',
        'httpx',
        'pypdf',
        'docx',
        'pytest'
    ]
    
    all_installed = True
    for package in required:
        try:
            __import__(package)
            print(f"   ✅ {package}")
        except ImportError:
            print(f"   ❌ {package} (not installed)")
            all_installed = False
    
    return all_installed


def check_env_file():
    """Check if .env file exists and has required variables"""
    print("\n⚙️  Checking environment configuration...")
    
    env_path = Path(".env")
    if not env_path.exists():
        print("   ❌ .env file not found")
        print("      Run: cp .env.example .env")
        return False
    
    print("   ✅ .env file exists")
    
    # Check if .env has required variables
    with open(env_path, 'r') as f:
        content = f.read()
    
    if 'OPENAI_API_KEY' in content:
        # Check if it's not the default value
        if 'your_openai_api_key_here' in content:
            print("   ⚠️  OPENAI_API_KEY is still set to default value")
            print("      Please update with your actual API key")
            return False
        else:
            print("   ✅ OPENAI_API_KEY is configured")
            return True
    else:
        print("   ❌ OPENAI_API_KEY not found in .env")
        return False


def check_file_structure():
    """Check if all required files exist"""
    print("\n📁 Checking file structure...")
    
    required_files = [
        'main.py',
        'config.py',
        'models.py',
        'file_utils.py',
        'openai_client.py',
        'requirements.txt',
        'README.md',
        'Dockerfile',
        'docker-compose.yml',
        'tests/conftest.py',
        'tests/test_main.py',
        'tests/test_file_utils.py',
        'tests/test_openai_client.py',
        'tests/mock_openai_response.json'
    ]
    
    all_exist = True
    for file_path in required_files:
        if Path(file_path).exists():
            print(f"   ✅ {file_path}")
        else:
            print(f"   ❌ {file_path} (missing)")
            all_exist = False
    
    return all_exist


def check_syntax():
    """Check Python syntax of main files"""
    print("\n🔍 Checking Python syntax...")
    
    files_to_check = [
        'main.py',
        'config.py',
        'models.py',
        'file_utils.py',
        'openai_client.py'
    ]
    
    all_valid = True
    for file_path in files_to_check:
        try:
            with open(file_path, 'r') as f:
                compile(f.read(), file_path, 'exec')
            print(f"   ✅ {file_path}")
        except SyntaxError as e:
            print(f"   ❌ {file_path}: {e}")
            all_valid = False
        except FileNotFoundError:
            print(f"   ❌ {file_path}: File not found")
            all_valid = False
    
    return all_valid


def check_imports():
    """Test if main modules can be imported"""
    print("\n📥 Checking module imports...")
    
    modules = ['config', 'models', 'file_utils', 'openai_client']
    
    all_importable = True
    for module in modules:
        try:
            __import__(module)
            print(f"   ✅ {module}")
        except Exception as e:
            print(f"   ❌ {module}: {e}")
            all_importable = False
    
    return all_importable


def print_summary(checks):
    """Print validation summary"""
    print("\n" + "="*60)
    print("📊 VALIDATION SUMMARY")
    print("="*60)
    
    passed = sum(checks.values())
    total = len(checks)
    
    for check, result in checks.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {check}")
    
    print("="*60)
    print(f"Result: {passed}/{total} checks passed")
    print("="*60)
    
    if passed == total:
        print("\n🎉 All checks passed! You're ready to start the server.")
        print("\nNext steps:")
        print("  1. python start.py        (Start development server)")
        print("  2. pytest                 (Run tests)")
        print("  3. python example_usage.py (See usage examples)")
        return True
    else:
        print("\n⚠️  Some checks failed. Please fix the issues above.")
        print("\nQuick fixes:")
        print("  - Install dependencies: pip install -r requirements.txt")
        print("  - Create .env file: cp .env.example .env")
        print("  - Set API key in .env: OPENAI_API_KEY=your_key")
        return False


def main():
    """Run all validation checks"""
    print("="*60)
    print("🔧 Resume Diff AI Backend - Validation Script")
    print("="*60)
    
    checks = {
        "Python Version": check_python_version(),
        "Dependencies": check_dependencies(),
        "Environment File": check_env_file(),
        "File Structure": check_file_structure(),
        "Python Syntax": check_syntax(),
        "Module Imports": check_imports()
    }
    
    success = print_summary(checks)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
