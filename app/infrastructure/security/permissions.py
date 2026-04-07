"""
security/permissions.py — Contrôle d'accès basé sur les rôles (RBAC).

Solution finale à l'import circulaire :
  On importe get_current_member directement depuis dependencies.py.
  dependencies.py importe depuis models.py → jwt.py → config.py.
  permissions.py n'est importé NI par dependencies.py NI par models.py.
  → Pas de circularité, l'import direct fonctionne.

Usage dans un router :
    from app.infrastructure.security.permissions import require_admin

    @router.post("/invite", dependencies=[Depends(require_admin)])
    async def invite(...):
        ...
"""

from fastapi import Depends

from app.dependencies import get_current_member
from app.shared.enums import Role
from app.shared.exceptions import ForbiddenError


async def require_admin(
    current_member=Depends(get_current_member),
) -> None:
    """
    Vérifie que le membre connecté est un administrateur.
    Lève ForbiddenError (HTTP 403) sinon.
    """
    if current_member.role != Role.ADMIN:
        raise ForbiddenError("Accès réservé aux administrateurs")


async def require_treasurer(
    current_member=Depends(get_current_member),
) -> None:
    """
    Vérifie que le membre est comptable ou administrateur.
    L'admin a accès à tout — il peut aussi confirmer des cotisations.
    """
    if current_member.role not in {Role.TREASURER, Role.ADMIN}:
        raise ForbiddenError("Accès réservé au comptable ou aux administrateurs")


async def require_manager(
    current_member=Depends(get_current_member),
) -> None:
    """
    Vérifie que le membre est gestionnaire ou administrateur.
    """
    if current_member.role not in {Role.MANAGER, Role.ADMIN}:
        raise ForbiddenError("Accès réservé aux gestionnaires ou aux administrateurs")