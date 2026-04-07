"""
security/jwt.py — Gestion des tokens JWT avec PyJWT.

PyJWT est la bibliothèque recommandée par la documentation officielle FastAPI.
Import : import jwt (et non plus from jose import jwt)

Différences avec python-jose :
  - jwt.encode()  retourne directement un str (pas besoin de .decode())
  - jwt.decode()  retourne un dict directement
  - Les exceptions sont sous jwt.PyJWTError (classe parente)
    → jwt.ExpiredSignatureError  : token expiré
    → jwt.InvalidTokenError      : token invalide (signature, format...)
    → jwt.DecodeError            : token malformé

Deux types de tokens dans notre système :
  - access_token  : courte durée (1h), envoyé dans chaque requête API
  - refresh_token : longue durée (30j), utilisé uniquement pour régénérer un access_token
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings
from app.shared.exceptions import InvalidTokenError


def _utc_now() -> datetime:
    """Retourne l'heure actuelle en UTC. Centralisé pour éviter les erreurs."""
    return datetime.now(timezone.utc)


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Génère un token d'accès JWT (courte durée).

    Args:
        subject      : l'identifiant unique du membre (UUID en string)
        extra_claims : données supplémentaires dans le token
                       (rôle, nom → évite une requête BDD à chaque requête)

    Payload généré :
        {
            "sub"  : "550e8400-...",   ← member_id
            "role" : "treasurer",
            "name" : "Mamadou Diallo",
            "exp"  : 1735000000,       ← timestamp d'expiration (géré par PyJWT)
            "iat"  : 1734996400,       ← timestamp de création
            "jti"  : "uuid-unique",    ← identifiant unique du token (anti-rejeu)
            "type" : "access"
        }

    jwt.encode(payload, key, algorithm) retourne directement un str.
    """
    expire = _utc_now() + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": _utc_now(),
        "jti": str(uuid.uuid4()),
        "type": "access",
    }

    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(subject: str) -> str:
    """
    Génère un token de rafraîchissement (longue durée).
    Contient moins d'infos que l'access token.
    """
    expire = _utc_now() + timedelta(days=settings.jwt_refresh_token_expire_days)

    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": _utc_now(),
        "jti": str(uuid.uuid4()),
        "type": "refresh",
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """
    Décode et valide un JWT avec PyJWT.

    PyJWT vérifie automatiquement :
      - La signature (clé secrète)
      - La date d'expiration (exp)
      - Le format du token

    Exceptions PyJWT capturées :
      - jwt.ExpiredSignatureError : token expiré
      - jwt.InvalidTokenError     : signature invalide, format incorrect...
      - jwt.DecodeError           : token malformé (pas un JWT valide)
      Toutes héritent de jwt.PyJWTError → on les attrape toutes avec PyJWTError

    Args:
        token         : le token JWT (string)
        expected_type : "access" ou "refresh"
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("Token expiré. Veuillez vous reconnecter.") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("Token invalide") from exc

    if payload.get("type") != expected_type:
        raise InvalidTokenError(
            f"Type de token invalide. "
            f"Attendu : '{expected_type}', reçu : '{payload.get('type')}'"
        )

    return payload


def extract_subject(token: str, expected_type: str = "access") -> str:
    """
    Raccourci : décode le token et retourne le subject (member_id).
    Utilisé dans get_current_member (dependencies.py).
    """
    payload = decode_token(token, expected_type)
    sub = payload.get("sub")
    if not sub:
        raise InvalidTokenError("Token sans identifiant (sub manquant)")
    return str(sub)