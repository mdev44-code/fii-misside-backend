"""
cache/redis.py — Interface avec Redis.

Redis est une base de données clé-valeur ultra-rapide (en mémoire).
On l'utilise pour :
  1. Stocker les refresh tokens JWT (avec expiration automatique)
  2. Stocker les tokens d'invitation (avec expiration 72h)
  3. Rate limiting (limiter le nombre de tentatives de connexion)

Exemple de données stockées :
  clé: "refresh_token:abc-123-uuid"   → valeur: "eyJhbGciOiJIUzI1NiJ..."
  clé: "invitation:xyz-token-123"     → valeur: "member-uuid-456"
  TTL (time to live) : la clé est supprimée automatiquement après X secondes.
"""

from typing import Any

import redis.asyncio as aioredis

from app.config import settings

# Variable globale : le client Redis partagé dans toute l'application
_redis_client: aioredis.Redis | None = None


async def init_redis() -> None:
    """
    Initialise la connexion Redis au démarrage de l'application.
    Appelé une seule fois dans le lifespan de main.py.
    """
    global _redis_client
    _redis_client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,  # retourne des str Python, pas des bytes
    )
    # ping() vérifie que Redis est bien accessible
    await _redis_client.ping()
    print("✅ Redis connecté")


async def close_redis() -> None:
    """Ferme proprement la connexion à l'arrêt de l'application."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        print("Redis déconnecté")


def get_redis() -> aioredis.Redis:
    """
    Retourne le client Redis initialisé.
    Lève une erreur si init_redis() n'a pas encore été appelé.
    """
    if not _redis_client:
        raise RuntimeError("Redis non initialisé. init_redis() doit être appelé au démarrage.")
    return _redis_client


# ── Helpers génériques ────────────────────────────────────────────────────────
# Ces fonctions wrappent les opérations Redis de base pour un usage plus simple.

async def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    """
    Stocke une valeur avec une durée de vie.
    setex = SET + EXpire : stocke ET définit l'expiration en une seule commande.

    Exemple :
        await cache_set("invitation:abc123", "member-uuid", 72 * 3600)
        # La clé sera automatiquement supprimée après 72 heures
    """
    await get_redis().setex(key, ttl_seconds, value)


async def cache_get(key: str) -> str | None:
    """
    Récupère une valeur par sa clé.
    Retourne None si la clé n'existe pas ou a expiré.
    """
    return await get_redis().get(key)


async def cache_delete(key: str) -> None:
    """Supprime une clé (ex: lors d'un logout, on supprime le refresh token)."""
    await get_redis().delete(key)


async def cache_exists(key: str) -> bool:
    """Vérifie si une clé existe encore (pas expirée)."""
    return bool(await get_redis().exists(key))


# ── Clés standardisées ────────────────────────────────────────────────────────
# CacheKeys centralise la construction des clés Redis.
# Avantage : pas de typo dispersées dans le code, tout est ici.
# Ex : au lieu d'écrire f"refresh_token:{member_id}" partout,
#      on écrit CacheKeys.refresh_token(member_id)

class CacheKeys:
    """Fabrique de clés Redis — nommage cohérent et centralisé."""

    @staticmethod
    def refresh_token(member_id: str) -> str:
        """
        Clé pour le refresh token d'un membre.
        Exemple : "refresh_token:550e8400-e29b-41d4-a716-446655440000"
        """
        return f"refresh_token:{member_id}"

    @staticmethod
    def invitation_token(token: str) -> str:
        """
        Clé pour un token d'invitation.
        Stocke l'id du membre invité.
        Exemple : "invitation:abc123xyz"
        """
        return f"invitation:{token}"

    @staticmethod
    def rate_limit(ip: str, action: str) -> str:
        """
        Clé pour le rate limiting par IP et action.
        Exemple : "rate_limit:login:192.168.1.1"
        """
        return f"rate_limit:{action}:{ip}"