"""Application configuration using pydantic BaseSettings.
All environment variables are loaded from a .env file in the project root.
Only the most used variables are defined here – you can extend as needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseSettings, Field, validator

class Settings(BaseSettings):
    # Core LLM settings
    LLM_MODE: Literal["mock", "local_only", "local_dual", "hybrid", "cloud_only", "replay"] = Field(
        default="mock",
        description="Mode of the LLM pipeline",
    )
    ALLOW_CLOUD: bool = Field(
        default=False,
        description="Allow cloud calls when mode permits",
    )
    LOCAL_LLM_BASE_URL: str = Field(
        default="http://127.0.0.1:8080/v1",
        description="Base URL for local LLM server",
    )
    CASE_LLM_BASE_URL: str = Field(
        default="https://changellenge.up.railway.app/v1",
        description="Base URL for cloud LLM",
    )
    CASE_LLM_API_KEY: str = Field(
        default="",
        description="API key for cloud LLM (empty means not set)",
    )
    # Database & paths
    APP_DB_PATH: str = Field(
        default="data/app.sqlite3",
        description="SQLite DB file path",
    )
    DATA_DIR: str = Field(default="data", description="Root data directory")
    CONFIG_DIR: str = Field(default="config", description="Configuration directory")
    # Misc
    LOG_LEVEL: str = Field(default="INFO", description="Logging level for the app")

    class Config:
        env_file = ".env"
        case_sensitive = False
        env_file_encoding = "utf-8"

    @validator("LLM_MODE")
    def _validate_mode(cls, v: str) -> str:
        # ensure lower‑case for consistency
        return v.lower()

# Export a singleton that can be imported elsewhere
settings = Settings()
