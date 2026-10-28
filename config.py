"""
Configuration management using pydantic-settings
Supports AWS Secrets Manager for Lambda deployment
"""
import os
import json
import logging
import secrets
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
            secrets_data = json.loads(response["SecretString"])
            logger.info(f"Successfully loaded secrets from AWS Secrets Manager")
            return secrets_data
        
    except Exception as e:
        logger.warning(f"Failed to fetch secrets from AWS Secrets Manager: {e}")
    
    return {}

# Load secrets from AWS if available
_aws_secrets = get_secrets_from_aws()


def _secret(name: str, default: str = "") -> str:
    """Read a value from AWS Secrets Manager (if enabled), else the default"""
    return str(_aws_secrets.get(name, default))


class Settings(BaseSettings):
    # OpenAI Configuration
    OPENAI_API_KEY: str = _aws_secrets.get("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = _aws_secrets.get("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_TIMEOUT: int = int(_aws_secrets.get("OPENAI_TIMEOUT", 25))
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
    
    # Deployment environment: "production" enforces required secrets at startup
    ENVIRONMENT: str = "production"

    # JWT Authentication Configuration (required in production, see _validate)
    JWT_SECRET_KEY: str = _aws_secrets.get("JWT_SECRET_KEY", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # MongoDB Atlas Configuration
    MONGODB_URI: str = _aws_secrets.get("MONGODB_URI", "")
    MONGODB_DATABASE: str = _aws_secrets.get("MONGODB_DATABASE", "resume_diff_ai")
    
    # Public URL of the frontend, used in emails and Stripe redirects
    FRONTEND_URL: str = "http://localhost:5173"

    # Email (verification and password reset). "console" logs instead of sending.
    EMAIL_BACKEND: str = "console"  # console | ses
    EMAIL_FROM: str = "Resume Diff AI <no-reply@example.com>"
    REQUIRE_EMAIL_VERIFICATION: bool = False

    # Google sign-in (OAuth client id used to validate ID tokens)
    GOOGLE_CLIENT_ID: str = _secret("GOOGLE_CLIENT_ID")

    # Stripe billing
    STRIPE_SECRET_KEY: str = _secret("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: str = _secret("STRIPE_WEBHOOK_SECRET")
    STRIPE_PRICE_PRO_MONTHLY: str = _secret("STRIPE_PRICE_PRO_MONTHLY")
    STRIPE_PRICE_PRO_YEARLY: str = _secret("STRIPE_PRICE_PRO_YEARLY")
    STRIPE_PRICE_PASS: str = _secret("STRIPE_PRICE_PASS")  # one-time, 90 days
    STRIPE_PRICE_COACH_MONTHLY: str = _secret("STRIPE_PRICE_COACH_MONTHLY")
    PASS_DURATION_DAYS: int = 90

    # Data retention for saved comparisons (privacy)
    HISTORY_RETENTION_DAYS: int = 365

    # Job URL import
    JOB_IMPORT_MAX_BYTES: int = 2 * 1024 * 1024
    JOB_IMPORT_TIMEOUT: int = 10

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse ALLOWED_ORIGINS into a list"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]
    
    class Config:
        env_file = ".env"
        case_sensitive = True


def _validate(cfg: Settings) -> Settings:
    """Fail fast on missing secrets instead of silently generating random ones.

    A random per-process JWT secret logs every user out on each Lambda cold start.
    """
    if not cfg.JWT_SECRET_KEY:
        if cfg.is_production:
            raise RuntimeError(
                "JWT_SECRET_KEY is not set. Configure it in the environment or AWS "
                "Secrets Manager (or set ENVIRONMENT=development for local use)."
            )
        logger.warning("JWT_SECRET_KEY not set; using an ephemeral key (development only)")
        cfg.JWT_SECRET_KEY = secrets.token_urlsafe(32)
    return cfg


settings = _validate(Settings())
