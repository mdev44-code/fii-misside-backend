"""
app/infrastructure/security/permissions.py — Fonctions de contrôle d'accès.

AJOUT : require_treasurer
  → Autorise les rôles : treasurer (comptable) + admin
  → Utilisé pour les routes de cotisation

Fonctions existantes (à conserver) :
  require_manager  → manager + admin
  require_admin    → admin uniquement

Usage dans un router :
  @router.post("", dependencies=[Depends(require_treasurer)])
  async def add_contribution(...):
      ...
"""

from fastapi import Depends, HTTPException, status

from app.dependencies import get_current_member
from app.shared.enums import Role


# ─────────────────────────────────────────────────────────────────────────────
# REQUIRE TREASURER — Comptable ou Admin
# ─────────────────────────────────────────────────────────────────────────────

async def require_treasurer(current_member=Depends(get_current_member)):
    """
    Vérifie que le membre connecté est un comptable (treasurer) ou un admin.

    Utilisé pour :
      POST /contributions → seul le comptable enregistre les cotisations

    Pourquoi treasurer ET admin ?
    → L'admin doit pouvoir tout faire en cas de besoin.
    → Le treasurer est le rôle métier dédié à la gestion financière.

    Raises:
      403 Forbidden si le rôle n'est pas autorisé
    """
    allowed_roles = {Role.TREASURER, Role.ADMIN}
    if current_member.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Accès refusé",
                "message": "Cette action est réservée au comptable (treasurer) et à l'administrateur",
                "your_role": current_member.role,
                "required_roles": [r.value for r in allowed_roles],
            },
        )
    return current_member


# ─────────────────────────────────────────────────────────────────────────────
# REQUIRE MANAGER — Manager ou Admin
# ─────────────────────────────────────────────────────────────────────────────

async def require_manager(current_member=Depends(get_current_member)):
    """
    Vérifie que le membre connecté est un manager ou un admin.

    Utilisé pour :
      POST/PATCH/DELETE /projects
    """
    allowed_roles = {Role.MANAGER, Role.ADMIN}
    if current_member.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Accès refusé",
                "message": "Cette action est réservée aux gestionnaires et à l'administrateur",
                "your_role": current_member.role,
                "required_roles": [r.value for r in allowed_roles],
            },
        )
    return current_member


# ─────────────────────────────────────────────────────────────────────────────
# REQUIRE ADMIN — Admin uniquement
# ─────────────────────────────────────────────────────────────────────────────

async def require_admin(current_member=Depends(get_current_member)):
    """
    Vérifie que le membre connecté est un administrateur.

    Utilisé pour les opérations les plus sensibles :
      - Inviter/suspendre/supprimer des membres
      - Modifier les paramètres de l'association
    """
    if current_member.role != Role.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Accès refusé",
                "message": "Cette action est réservée à l'administrateur",
                "your_role": current_member.role,
                "required_roles": [Role.ADMIN.value],
            },
        )
    return current_member