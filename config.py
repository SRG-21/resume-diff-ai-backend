"""
Configuration management using pydantic-settings
Supports AWS Secrets Manager for Lambda deployment
"""
import os
import json
import logging
from pydantic_settings import BaseSettings
from typing import List, Optional

logger = logging.getLogger(__name__)

def get_secrets_from_aws() -> dict:
    """
    Fetch secrets from AWS Secrets Manager
    Returns empty dict if not in AWS environment or on failure
    """
    if os.environ.get("USE_SECRETS_MANAGER") != "true":
        return {}
    
    try:
        import boto3
        from botocore.exceptions import ClientError
        
        secret_name = os.environ.get("SECRET_NAME", "resume-diff-ai/secrets")
        region = os.environ.get("AWS_REGION", "us-east-1")
        
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=secret_name)
        
        if "SecretString" in response:
            secrets = json.loads(response["SecretString"])
            logger.info(f"Successfully loaded secrets from AWS Secrets Manager")
            return secrets
        
    except Exception as e:
        logger.warning(f"Failed to fetch secrets from AWS Secrets Manager: {e}")
    
    return {}

# Load secrets from AWS if available
_aws_secrets = get_secrets_from_aws()


class Settings(BaseSettings):
    # OpenAI Configuration
    OPENAI_API_KEY: str = _aws_secrets.get("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = _aws_secrets.get("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TIMEOUT: int = int(_aws_secrets.get("OPENAI_TIMEOUT", 60))
    OPENAI_MAX_TOKENS: int = int(_aws_secrets.get("OPENAI_MAX_TOKENS", 2000))
    
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
