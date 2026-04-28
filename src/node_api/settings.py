from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Minimal env-var driven config for the API skeleton.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: Literal["dev", "prod"] = Field(default="dev", validation_alias="APP_ENV")
    port: int = Field(default=8080, validation_alias="PORT")
    api_v1_prefix: str = Field(default="/v1", validation_alias="API_V1_PREFIX")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    translator_log_path: str | None = Field(default=None, validation_alias="TRANSLATOR_LOG_PATH")
    translator_log_default_lines: int = Field(
        default=200, ge=1, validation_alias="TRANSLATOR_LOG_DEFAULT_LINES"
    )
    translator_log_max_lines: int = Field(
        default=1000, ge=1, validation_alias="TRANSLATOR_LOG_MAX_LINES"
    )
    translator_monitoring_base_url: str | None = Field(
        default=None, validation_alias="TRANSLATOR_MONITORING_BASE_URL"
    )
    translator_monitoring_timeout_secs: float = Field(
        default=3.0, ge=0.1, validation_alias="TRANSLATOR_MONITORING_TIMEOUT_SECS"
    )
    translator_blocks_found_db_path: str = Field(
        default=".data/translator_blocks_found.sqlite3",
        validation_alias="TRANSLATOR_BLOCKS_FOUND_DB_PATH",
    )

    # Auth (stub)
    auth_mode: Literal["dev_token", "jwt"] | None = Field(
        default=None, validation_alias="AUTH_MODE"
    )
    az_api_dev_token: str | None = Field(default=None, validation_alias="AZ_API_DEV_TOKEN")

    # AZCoin RPC (used by `/v1/az/*`)
    az_rpc_url: str | None = Field(default=None, validation_alias="AZ_RPC_URL")
    az_rpc_user: str | None = Field(default=None, validation_alias="AZ_RPC_USER")
    az_rpc_password: SecretStr | None = Field(
        default=None, validation_alias="AZ_RPC_PASSWORD", repr=False
    )
    az_rpc_timeout_seconds: float = Field(default=5.0, validation_alias="AZ_RPC_TIMEOUT_SECONDS")
    az_expected_chain: str = Field(default="main", validation_alias="AZ_EXPECTED_CHAIN")

    # Reward ownership matching for `/v1/az/blocks/rewards`. Comma-separated;
    # whitespace is trimmed and empty entries are dropped at the route layer.
    # Addresses match exactly; scriptPubKey hex matches case-insensitively.
    az_reward_ownership_addresses: str | None = Field(
        default=None, validation_alias="AZ_REWARD_OWNERSHIP_ADDRESSES"
    )
    az_reward_ownership_script_pubkeys: str | None = Field(
        default=None, validation_alias="AZ_REWARD_OWNERSHIP_SCRIPT_PUBKEYS"
    )

    # Bitcoin RPC (used by `/v1/btc/*`)
    btc_rpc_url: str | None = Field(default=None, validation_alias="BTC_RPC_URL")
    btc_rpc_cookie_file: str | None = Field(
        default=None, validation_alias="BTC_RPC_COOKIE_FILE"
    )
    btc_rpc_user: str | None = Field(default=None, validation_alias="BTC_RPC_USER")
    btc_rpc_password: SecretStr | None = Field(
        default=None, validation_alias="BTC_RPC_PASSWORD", repr=False
    )
    btc_rpc_timeout_seconds: float = Field(default=5.0, validation_alias="BTC_RPC_TIMEOUT_SECONDS")

    @field_validator("translator_log_path", mode="before")
    @classmethod
    def _blank_translator_log_path(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip() if isinstance(value, str) else value

    @field_validator("translator_monitoring_base_url", mode="before")
    @classmethod
    def _blank_translator_monitoring_base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip().rstrip("/") if isinstance(value, str) else value

    @field_validator("translator_blocks_found_db_path", mode="before")
    @classmethod
    def _blank_translator_blocks_found_db_path(cls, value: object) -> str:
        if value is None:
            return ".data/translator_blocks_found.sqlite3"
        if isinstance(value, str) and not value.strip():
            return ".data/translator_blocks_found.sqlite3"
        return str(value).strip() if isinstance(value, str) else str(value)

    @model_validator(mode="before")
    @classmethod
    def _default_auth_mode(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        # If AUTH_MODE isn't set, default depends on APP_ENV
        if data.get("AUTH_MODE") is None and data.get("auth_mode") is None:
            app_env = (data.get("APP_ENV") or data.get("app_env") or "dev").lower()
            data["AUTH_MODE"] = "jwt" if app_env == "prod" else "dev_token"

        return data

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        if self.app_env == "prod" and self.auth_mode != "jwt":
            raise ValueError("Invalid AUTH_MODE: in APP_ENV=prod, AUTH_MODE must be 'jwt'.")

        if self.auth_mode == "dev_token":
            if not self.az_api_dev_token:
                raise ValueError(
                    "Missing AZ_API_DEV_TOKEN: required when AUTH_MODE='dev_token' (no default)."
                )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
