"""
config.py — Configuration centralisée de l'application.

Pydantic-settings lit automatiquement le fichier .env et valide
chaque valeur selon son type Python. Si une variable obligatoire
est manquante, l'application refuse de démarrer avec un message clair.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Toutes les variables de configuration de l'application.

    BaseSettings de pydantic-settings lit automatiquement le .env
    et convertit les valeurs dans le bon type Python.
    Ex : DEBUG="true" dans .env → self.debug = True (bool) en Python.
    """

    # On indique à Pydantic où trouver le fichier .env
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # DATABASE_URL = database_url, peu importe la casse
        extra="ignore",        # ignore les variables inconnues dans .env
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = "Fii-Misside"
    app_version: str = "1.0.0"
    # Literal["a", "b"] signifie : seules ces valeurs sont acceptées
    environment: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    frontend_url: str = "http://localhost:4200"

    # ── API ───────────────────────────────────────────────────────────────────
    api_prefix: str = "/api/v1"
    # Stocké comme string dans .env, on le convertit en liste dans la property
    allowed_origins: str = "http://localhost:4200"

    # ── Base de données ───────────────────────────────────────────────────────
    database_url: str
    database_url_sync: str

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # ── Invitations ───────────────────────────────────────────────────────────
    invitation_token_expire_hours: int = 72

    # ── Cotisations ───────────────────────────────────────────────────────────
    contribution_mode: Literal["free", "fixed"] = "free"
    contribution_fixed_amount: int = 5000
    contribution_reminder_start_day: int = 1
    contribution_reminder_end_day: int = 15
    contribution_reminder_hour: int = 9

    # ── AWS S3 ────────────────────────────────────────────────────────────────────
    aws_access_key_id: str = Field(default="", env="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(default="", env="AWS_SECRET_ACCESS_KEY")
    aws_s3_bucket_name: str = Field(default="", env="AWS_S3_BUCKET_NAME")
    aws_s3_region: str = Field(default="eu-west-3", env="AWS_S3_REGION")
    aws_s3_endpoint_url: str | None = Field(default=None, env="AWS_S3_ENDPOINT_URL")

    # ── Admin initial ─────────────────────────────────────────────────────────
    first_admin_phone: str = ""
    first_admin_email: str = ""
    first_admin_password: str = ""

    # ── Validators ───────────────────────────────────────────────────────────
    @field_validator("jwt_secret_key")
    @classmethod
    def secret_key_must_be_strong(cls, v: str) -> str:
        """
        @classmethod signifie que ce validator reçoit la classe, pas l'instance.
        Il s'exécute avant la création de l'objet Settings.
        Si la clé est trop courte, l'app refuse de démarrer.
        """
        if len(v) < 16:
            raise ValueError("JWT_SECRET_KEY doit faire au moins 16 caractères")
        return v

    # ── Properties calculées ──────────────────────────────────────────────────
    @property
    def cors_origins(self) -> list[str]:
        """
        Convertit la string "http://a.com,http://b.com"
        en liste Python ["http://a.com", "http://b.com"].
        Appelé comme un attribut : settings.cors_origins
        """
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


# ── Instance unique ───────────────────────────────────────────────────────────
@lru_cache
def get_settings() -> Settings:
    """
    @lru_cache : Python met le résultat en cache après le premier appel.
    Tous les appels suivants retournent la même instance sans relire le .env.

    Usage dans une route FastAPI :
        from fastapi import Depends
        def ma_route(s: Settings = Depends(get_settings)):
            ...

    Usage direct (hors FastAPI) :
        from app.config import settings
        print(settings.app_name)
    """
    return Settings()


# Raccourci pratique — importable directement
settings = get_settings()