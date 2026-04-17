"""Configuration management via pydantic-settings.

Precedence (highest to lowest):
  1. CLI flag overrides (applied in cli/app.py callback)
  2. Environment variables (``AUTOMIC_*``)
  3. ``.env`` file
  4. ``aromic.yaml`` / ``aromic.yml`` / ``$HOME/.config/aromic/config.yaml``
     (or the path given by ``AROMIC_CONFIG_FILE``)
  5. Built-in defaults
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutomicSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOMIC_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # ---- connection ----------------------------------------------------
    url: str = "http://localhost:8080/ae/api/v1"
    client_id: int = 100
    user: str = ""
    department: str = ""
    password: SecretStr = SecretStr("")
    verify_ssl: bool = True
    timeout: int = 30
    max_retries: int = 3

    # ---- folder behaviour ---------------------------------------------
    # When True (default) the applier relies on Automic's import endpoint
    # to create folder paths automatically.  When False and a new folder
    # path is encountered the applier surfaces a FolderMissingError rather
    # than attempting the write.
    auto_create_folders: bool = True

    # ---- retry knobs --------------------------------------------------
    # First backoff delay (ms) when 429 arrives without a Retry-After header.
    retry_base_delay_ms: int = 500
    # Upper cap on any single backoff sleep (seconds).
    retry_max_backoff_s: float = 5.0
    # Which HTTP status codes trigger the retry loop.
    retry_statuses: list[int] = [429]

    # ---- write strategy -----------------------------------------------
    # POST_IMPORT: POST /objects?overwrite_existing_objects=true (swagger v21 canonical).
    # PUT: legacy assumption; kept so operators with non-standard instances can flip.
    update_method: Literal["POST_IMPORT", "PUT"] = "POST_IMPORT"

    # ---- authentication -----------------------------------------------
    # basic: HTTP Basic per swagger v21.
    # bearer: stub for Automic 24.2+ bearer token support (not yet implemented).
    auth_method: Literal["basic", "bearer"] = "basic"
