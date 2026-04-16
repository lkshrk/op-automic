"""Configuration management via pydantic-settings."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutomicSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOMIC_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    url: str = "http://localhost:8080/ae/api/v1"
    client_id: int = 100
    user: str = ""
    department: str = ""
    password: SecretStr = SecretStr("")
    verify_ssl: bool = True
    timeout: int = 30
    max_retries: int = 3
