"""
Configuration management using pydantic-settings
"""
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # OpenAI Configuration
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"  # Completions-capable model
    OPENAI_TIMEOUT: int = 60  # seconds
    OPENAI_MAX_TOKENS: int = 2000
    
    # File Upload Configuration
    MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10 MB
    MAX_TEXT_LENGTH: int = 100000  # 100k characters
    TEMP_DIR: str = "temp_uploads"
    
    # CORS Configuration
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse ALLOWED_ORIGINS into a list"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
