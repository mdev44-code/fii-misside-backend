"""
members/router.py — Endpoints HTTP pour la gestion des membres.

Contrôle d'accès par route :
  GET  /members              → tous les membres connectés (voir la liste)
  GET  /members/org-chart    → tous les membres connectés (voir l'organigramme)
  GET  /members/{id}         → tous les membres connectés
  POST /members/invite       → admin uniquement
  PATCH /members/{id}/role   → admin uniquement
  PATCH /members/{id}/status → admin uniquement

Deux façons d'appliquer le contrôle d'accès dans FastAPI :

  1. Via dependencies= dans le décorateur (route entière protégée)
     @router.post("/invite", dependencies=[Depends(require_admin)])
     → FastAPI exécute require_admin AVANT d'appeler la fonction
     → Si le rôle est incorrect → 403 automatique, la fonction n'est pas appelée

  2. Via un paramètre dans la fonction (si on a besoin du résultat)
     async def ma_route(admin: Member = Depends(require_admin)):
     → Même protection + le membre admin est disponible dans la fonction
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.members.schemas import (
    InviteMemberRequest,
    UpdateMemberRoleRequest,
    UpdateMemberStatusRequest,
)
from app.domains.members.service import MemberService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_admin
from app.shared.response import success_response

router = APIRouter()


@router.get("/org-chart")
async def get_org_chart(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne l'organigramme groupé par rôle.
    Accessible à tous les membres connectés.
    """
    service = MemberService(db)
    chart = await service.get_org_chart()

    return success_response(
        data=chart.model_dump(),
        message="Organigramme récupéré",
    )


@router.get("")
async def list_members(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Liste tous les membres de l'association.
    Accessible à tous les membres connectés.
    """
    service = MemberService(db)
    members = await service.get_all_members()

    return success_response(
        data=[m.model_dump() for m in members],
        message=f"{len(members)} membre(s) trouvé(s)",
    )


@router.post("/invite", dependencies=[Depends(require_admin)])
async def invite_member(
    data: InviteMemberRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Crée une invitation pour un nouveau membre.
    Réservé aux administrateurs.

    Retourne le lien d'invitation à partager par WhatsApp/SMS.

    Note : on passe current_member au service car on en a besoin
    pour l'audit log (qui a créé cette invitation ?).
    """
    service = MemberService(db)
    result = await service.invite_member(data, invited_by=current_member)

    return success_response(
        data=result.model_dump(),
        message=result.message,
    )


@router.get("/{member_id}")
async def get_member(
    member_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne les détails d'un membre par son ID.
    Accessible à tous les membres connectés.
    """
    service = MemberService(db)
    member = await service.get_member_by_id(member_id)

    return success_response(data=member.model_dump())


@router.patch("/{member_id}/role", dependencies=[Depends(require_admin)])
async def update_member_role(
    member_id: str,
    data: UpdateMemberRoleRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Change le rôle d'un membre.
    Réservé aux administrateurs.

    Règles métier appliquées dans le service :
      - Impossible de changer son propre rôle
      - Impossible de retirer le rôle du seul admin
    """
    service = MemberService(db)
    member = await service.update_role(
        member_id=member_id,
        new_role=data.role,
        updated_by=current_member,
    )

    return success_response(
        data=member.model_dump(),
        message=f"Rôle mis à jour : {member.role}",
    )


@router.patch("/{member_id}/status", dependencies=[Depends(require_admin)])
async def update_member_status(
    member_id: str,
    data: UpdateMemberStatusRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Change le statut d'un membre (active/inactive/suspended).
    Réservé aux administrateurs.
    """
    service = MemberService(db)
    member = await service.update_status(
        member_id=member_id,
        new_status=data.status,
        updated_by=current_member,
    )

    return success_response(
        data=member.model_dump(),
        message=f"Statut mis à jour : {member.status}",
    )