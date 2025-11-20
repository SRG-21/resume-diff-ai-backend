"""
Quick start script for Resume Diff AI Backend
Run this to start the development server
"""
import os
import sys
from pathlib import Path


def check_env_file():
    """Check if .env file exists"""
    env_path = Path(".env")
    env_example_path = Path(".env.example")
    
    if not env_path.exists():
        print("❌ .env file not found!")
        if env_example_path.exists():
            print("📝 Creating .env from .env.example...")
            with open(env_example_path, 'r') as src, open(env_path, 'w') as dst:
                dst.write(src.read())
            print("✅ .env file created")
            print("\n⚠️  IMPORTANT: Update your OPENAI_API_KEY in .env file!")
            return False
        else:
            print("❌ .env.example not found either!")
            return False
    return True


def check_openai_key():
    """Check if OpenAI API key is set"""
    from dotenv import load_dotenv
    load_dotenv()
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    
    if not api_key or api_key == "your_openai_api_key_here":
        print("\n⚠️  WARNING: OPENAI_API_KEY is not set or is using default value")
        print("Please update your .env file with a valid OpenAI API key")
        return False
    
    print(f"✅ OpenAI API key found (starts with: {api_key[:10]}...)")
    return True


def start_server():
    """Start the FastAPI server"""
    import uvicorn
    from config import settings
    
    print("\n" + "="*60)
    print("🚀 Starting Resume Diff AI Backend")
    print("="*60)
    print(f"📍 Server: http://{settings.HOST}:{settings.PORT}")
    print(f"📚 Docs: http://localhost:{settings.PORT}/docs")
    print(f"🏥 Health: http://localhost:{settings.PORT}/health")
    print(f"🤖 Model: {settings.OPENAI_MODEL}")
    print("="*60 + "\n")
    
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower()
    )


if __name__ == "__main__":
    print("Resume Diff AI Backend - Quick Start")
    print("="*60 + "\n")
    
    # Check environment setup
    if not check_env_file():
        sys.exit(1)
    
    # Check API key (warning only, not blocking)
    check_openai_key()
    
    # Start server
    try:
        start_server()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped by user")
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        sys.exit(1)
