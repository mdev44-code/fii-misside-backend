"""
app/dependencies.py — Dépendances globales réutilisées dans tous les routers.

Une "dependency" FastAPI est une fonction injectée automatiquement
dans les routes via Depends(). Elle s'exécute avant le code de la route.

get_current_member est la dependency la plus importante :
elle vérifie le token JWT et retourne le membre connecté.
"""

import uuid

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.session import get_db
from app.infrastructure.security.jwt import extract_subject
from app.shared.enums import MemberStatus
from app.shared.exceptions import ForbiddenError, NotFoundError, UnauthorizedError

# HTTPBearer : extrait automatiquement le token du header Authorization.
# Le client doit envoyer : Authorization: Bearer eyJhbGc...
# auto_error=True : si le header est absent → erreur 403 automatique
security = HTTPBearer(auto_error=True)


async def get_current_member(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency principale : valide le token et retourne le membre connecté.

    Étapes :
      1. HTTPBearer extrait le token du header Authorization
      2. extract_subject() décode le JWT et récupère le member_id
      3. On charge le membre depuis la BDD
      4. On vérifie que son compte est actif

    Cette dependency est chaînée : elle dépend elle-même de HTTPBearer
    et de get_db. FastAPI résout toutes ces dépendances automatiquement.

    Retourne : l'objet Member SQLAlchemy (pas un schema Pydantic)
    → Le service peut l'utiliser directement pour des requêtes BDD.
    """
    # Import ici pour éviter les imports circulaires
    # (models.py → enums.py → pas de retour vers dependencies.py)
    from app.infrastructure.database.models import Member

    # 1. Décode le JWT et extrait le member_id (le "sub" du token)
    member_id_str = extract_subject(credentials.credentials)

    # 2. Convertit la string en UUID Python
    try:
        member_id = uuid.UUID(member_id_str)
    except ValueError as exc:
        raise UnauthorizedError("Token malformé") from exc

    # 3. Charge le membre depuis la BDD
    # scalar_one_or_none() : retourne l'objet ou None (pas d'exception si absent)
    result = await db.execute(
        select(Member).where(Member.id == member_id)
    )
    member = result.scalar_one_or_none()

    if not member:
        raise NotFoundError("Membre")

    # 4. Vérifie que le compte est actif
    if member.status != MemberStatus.ACTIVE:
        raise ForbiddenError(
            "Votre compte est inactif ou suspendu. "
            "Contactez l'administrateur."
        )

    return member