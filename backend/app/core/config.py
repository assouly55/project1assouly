"""
Tender AI Platform - Configuration
All settings loaded from environment variables
"""

from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Application
    APP_NAME: str = "Tender AI Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    
    # Database
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/tender_ai"
    
    # DeepSeek AI (for text processing)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    
    # Scraper Configuration
    SCRAPER_HEADLESS: bool = False  # TEMP: Disabled for debugging popup click
    SCRAPER_MAX_CONCURRENT: int = 5
    SCRAPER_RETRY_ATTEMPTS: int = 3
    SCRAPER_TIMEOUT_PAGE: int = 30000  # ms
    SCRAPER_TIMEOUT_DOWNLOAD: int = 60000  # ms
    
    # Target Site
    TARGET_HOMEPAGE: str = "https://www.marchespublics.gov.ma/pmmp/"
    TARGET_LINK_PREFIX: str = "https://www.marchespublics.gov.ma/index.php?page=entreprise.EntrepriseDetailConsultation&refConsultation="
    
    # Form data for download
    FORM_NOM: str = "Assouly"
    FORM_PRENOM: str = "Ben Ahmed"
    FORM_EMAIL: str = "test@test.com"
    
    # Category filter (2 = Fournitures)
    CATEGORY_FILTER: str = "2"
    
    # Test mode - run immediately instead of scheduled
    TEST_MODE: bool = True
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance"""
    return Settings()


settings = get_settings()