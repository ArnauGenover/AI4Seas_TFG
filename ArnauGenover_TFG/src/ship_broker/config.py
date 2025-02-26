# src/ship_broker/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    PROJECT_NAME: str = "Ship Broker"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    
    # Database settings
    SQLALCHEMY_DATABASE_URL: str = "sqlite:///./src/ship_broker/ship_broker.db"
    
    # Email settings
    EMAIL_ADDRESS: str = ""
    EMAIL_PASSWORD: str = ""
    IMAP_SERVER: str = "imap.gmail.com"
    EMAIL_CHECK_INTERVAL: int = int(os.getenv("EMAIL_CHECK_INTERVAL", "300"))  # 5 minutes default
    
    # OpenAI settings
    OPENAI_API_KEY: str = ""
    
    # Auction settings
    AUCTION_DURATION_DAYS: int = int(os.getenv("AUCTION_DURATION_DAYS", "15"))
    AUCTION_START_PRICE: float = float(os.getenv("AUCTION_START_PRICE", "20.0"))  # USD per MT
    AUCTION_MIN_PRICE: float = float(os.getenv("AUCTION_MIN_PRICE", "10.0"))  # USD per MT
    
    # Authentication settings
    SECRET_KEY: str = os.getenv("SECRET_KEY", "BarcelonaMadrid2025.")  # Use env var if available
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    
    # SMTP Email Settings
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    
    # Encryption settings
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "YourEncryptionKeyHere-Must32BytesLong=")  # Use env var if available
    
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore"
    )

@lru_cache()
def get_settings():
    return Settings()

# Create the settings instance that can be imported
settings = get_settings()