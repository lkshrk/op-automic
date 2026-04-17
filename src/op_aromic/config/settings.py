"""Configuration management via pydantic-settings.

Precedence (highest to lowest):
  1. CLI flag overrides (applied in cli/app.py callback)
  2. Environment variables (``AUTOMIC_*``)
  3. ``.env`` file
  4. ``aromic.yaml`` / ``aromic.yml`` / ``$HOME/.config/aromic/config.yaml``
     (or the path given by ``AROMIC_CONFIG_FILE``)
  5. Built-in defaults

The YAML config file is found via the first match of:
  - ``$AROMIC_CONFIG_FILE`` environment variable
  - ``./aromic.yaml``
  - ``./aromic.yml``
  - ``$HOME/.config/aromic/config.yaml``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


def _find_config_file() -> Path | None:
    """Return the first config file found on the search path, or None."""
    override = os.environ.get("AROMIC_CONFIG_FILE")
    if override:
        return Path(override)
    candidates = [
        Path("aromic.yaml"),
        Path("aromic.yml"),
        Path.home() / ".config" / "aromic" / "config.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class _YamlSource(PydanticBaseSettingsSource):
    """Read settings from an ``aromic.yaml`` (or equivalent) file."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        path = _find_config_file()
        if path is None:
            return {}
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(
                f"Failed to parse YAML config at {path}: {exc}"
            ) from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(
                f"YAML config at {path} must be a mapping, got {type(data).__name__}"
            )
        return data

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:
        """Return (value, field_key, value_is_complex) for a single field."""
        value = self._data.get(field_name)
        return value, field_name, self.field_is_complex(field)

    def __call__(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field_info, field_name)
            if value is not None:
                d[key] = value
        return d


class AutomicSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOMIC_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: init > env > dotenv > yaml > defaults
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSource(settings_cls),
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
