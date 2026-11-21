"""Centralised, environment-driven configuration.

Every tunable lives here so no other module reads ``os.environ`` directly.
"""

from __future__ import annotations

import math
import os
import socket
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_INSECURE_MARKERS = ("change-me", "changeme", "dev-only", "example")


def _csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- General -----------------------------------------------------------
    app_name: str = "url-shortener"
    environment: Literal["development", "test", "production"] = "development"
    base_url: str = "http://localhost:8000"  # public origin that serves short links
    log_level: str = "INFO"
    log_json: bool = True
    access_log_enabled: bool = True
    max_request_body_bytes: int = 64 * 1024
    hsts_enabled: bool = False

    # --- PostgreSQL --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://shortener:shortener@localhost:5432/shortener"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 5.0
    db_command_timeout_seconds: float = 10.0

    # --- Redis (three isolated clients) ------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    cache_redis_url: str | None = None
    ratelimit_redis_url: str | None = None
    stream_redis_url: str | None = None
    redis_socket_timeout_ms: int = 250
    redis_max_connections: int = 100
    redis_breaker_failures: int = 5
    redis_breaker_cooldown_seconds: float = 2.0

    # --- Auth --------------------------------------------------------------
    secret_key: SecretStr = SecretStr("dev-only-change-me-secret-key-0123456789")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "url-shortener"
    access_token_expire_minutes: int = 60
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4
    password_min_length: int = 8

    # --- Short codes -------------------------------------------------------
    id_allocator_name: str = "short_codes"
    id_range_size: int = 1000
    # Bijective scrambling so sequential IDs do not yield guessable codes.
    # NEVER change these on a database that already contains generated codes.
    id_scramble_multiplier: int = 2654435761
    id_scramble_offset: int = 987654321
    anonymous_creation_enabled: bool = True
    max_url_length: int = 2048
    max_expiry_days: int = 3650

    # --- Cache -------------------------------------------------------------
    cache_enabled: bool = True
    negative_cache_enabled: bool = True
    cache_ttl_seconds: int = 24 * 3600
    negative_cache_ttl_seconds: int = 60

    # --- Rate limiting -----------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_fail_open: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_create_per_window: int = 10
    rate_limit_create_user_per_window: int = 60
    rate_limit_redirect_per_window: int = 120
    rate_limit_auth_per_window: int = 10
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Analytics pipeline -----------------------------------------------
    analytics_enabled: bool = True
    click_stream_name: str = "click_events"
    click_dead_letter_stream: str = "click_events_dead"
    click_stream_maxlen: int = 1_000_000
    stream_enqueue_timeout_ms: int = 50
    ip_hash_secret: SecretStr = SecretStr("dev-only-change-me-ip-hash-secret-0123456789")
    geoip_db_path: str | None = None
    store_event_details: bool = False  # keep UA / ip_hash / referrer on raw rows
    click_event_retention_days: int = 7

    # --- Worker ------------------------------------------------------------
    worker_group: str = "analytics"
    worker_consumer_name: str = Field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
    worker_batch_size: int = 500
    worker_flush_interval_seconds: float = 5.0
    worker_claim_idle_ms: int = 60_000
    worker_claim_interval_seconds: float = 15.0
    worker_metrics_port: int = 9101

    # --- Cleanup -----------------------------------------------------------
    cleanup_interval_seconds: float = 60.0
    cleanup_batch_size: int = 1000
    cleanup_cache_lookback_seconds: int = 600
    expired_url_purge_days: int = 0  # 0 disables hard deletion of expired URLs

    # --- HTTP --------------------------------------------------------------
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )

    @field_validator("trusted_proxies", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        return _csv(value)

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if math.gcd(self.id_scramble_multiplier, 62) != 1:
            raise ValueError("id_scramble_multiplier must be coprime with 62 to stay bijective")
        if self.environment == "production":
            for name in ("secret_key", "ip_hash_secret"):
                secret = getattr(self, name).get_secret_value()
                if len(secret) < 32 or any(m in secret.lower() for m in _INSECURE_MARKERS):
                    raise ValueError(f"{name} must be a unique random value of 32+ characters in production")
        return self

    # Helpers -------------------------------------------------------------
    @property
    def cache_url(self) -> str:
        return self.cache_redis_url or self.redis_url

    @property
    def ratelimit_url(self) -> str:
        return self.ratelimit_redis_url or self.redis_url

    @property
    def stream_url(self) -> str:
        return self.stream_redis_url or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
