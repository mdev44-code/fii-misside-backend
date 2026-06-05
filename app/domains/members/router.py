from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.members.schemas import (
    CreateGroupInviteRequest,
    InviteMemberRequest,
    UpdateMemberRoleRequest,
    UpdateMemberStatusRequest,
    UpdateProfileRequest,
)
from app.domains.members.service import MemberService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_admin
from app.shared.exceptions import BusinessRuleError
from app.shared.response import success_response

router = APIRouter()

# Taille max d'upload : 5 Mo
MAX_UPLOAD_SIZE = 5 * 1024 * 1024


# ─────────────────────────────────────────────────────────────────────────────
# GET /members/me — Profil du membre connecté
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/me")
async def get_my_profile(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    profile = await service.get_me(current_member)
    return success_response(data=profile.model_dump(), message="Profil chargé")


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /members/me — Modifier son profil
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/me")
async def update_my_profile(
    data: UpdateProfileRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    updated = await service.update_profile(current_member, data)
    await db.commit()
    return success_response(
        data=updated.model_dump(),
        message="Profil mis à jour avec succès",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /members/me/avatar — Uploader sa photo de profil
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/me/avatar")
async def upload_my_avatar(
    file: UploadFile = File(..., description="Image JPG, PNG ou WebP, max 5 Mo"),
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    file_content = await file.read()

    if len(file_content) > MAX_UPLOAD_SIZE:
        raise BusinessRuleError("La photo ne doit pas dépasser 5 Mo")

    if len(file_content) == 0:
        raise BusinessRuleError("Le fichier est vide")

    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "avatar"

    service = MemberService(db)
    updated = await service.upload_avatar(
        member=current_member,
        file_content=file_content,
        content_type=content_type,
        filename=filename,
    )
    await db.commit()

    return success_response(
        data=updated.model_dump(),
        message="Photo de profil mise à jour avec succès",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /members/org-chart — Organigramme
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/org-chart")
async def get_org_chart(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    chart = await service.get_org_chart()

    return success_response(
        data=chart.model_dump(),
        message="Organigramme récupéré",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /members — Liste de tous les membres
# ─────────────────────────────────────────────────────────────────────────────


@router.get("")
async def list_members(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Liste tous les membres actifs de l'association.
    Accessible à tous les membres connectés.
    """
    service = MemberService(db)
    members = await service.get_all_members()
    return success_response(
        data=[m.model_dump() for m in members],
        message=f"{len(members)} membre(s) trouvé(s)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /members/invite — Inviter un membre
# ─────────────────────────────────────────────────────────────────────────────


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
    """
    service = MemberService(db)
    result = await service.invite_member(data, invited_by=current_member)

    return success_response(
        data=result.model_dump(),
        message=result.message,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /members/group-invite — Créer un lien d'invitation groupé
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/group-invite", dependencies=[Depends(require_admin)])
async def create_group_invite(
    data: CreateGroupInviteRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    result = await service.create_group_invite(data, created_by=current_member)
    await db.commit()

    return success_response(
        data=result.model_dump(),
        message="Lien d'invitation groupé créé. Partagez-le dans votre groupe.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /members/group-invite — Lister les liens actifs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/group-invite", dependencies=[Depends(require_admin)])
async def list_group_invites(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    active_only: bool = Query(default=True),
):
    service = MemberService(db)
    invites = await service.list_group_invites(
        created_by=current_member,
        active_only=active_only,
    )

    return success_response(
        data=[inv.model_dump() for inv in invites],
        message=f"{len(invites)} lien(s) d'invitation trouvé(s)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /members/group-invite/{invite_id} — Désactiver un lien
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/group-invite/{invite_id}", dependencies=[Depends(require_admin)])
async def deactivate_group_invite(
    invite_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    result = await service.deactivate_group_invite(invite_id, requested_by=current_member)
    await db.commit()

    return success_response(
        data=result.model_dump(),
        message="Lien d'invitation désactivé avec succès.",
    )


@router.delete("/group-invite/{invite_id}/delete", dependencies=[Depends(require_admin)])
async def delete_group_invite(
    invite_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    await service.delete_group_invite(invite_id, requested_by=current_member)
    await db.commit()

    return success_response(
        message="Lien d'invitation supprimé définitivement.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Routes dynamiques /{member_id} — TOUJOURS EN DERNIER
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{member_id}")
async def get_member(
    member_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
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


@router.delete("/{member_id}", dependencies=[Depends(require_admin)])
async def delete_member(
    member_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = MemberService(db)
    await service.delete_member(
        member_id=member_id,
        deleted_by=current_member,
    )
    await db.commit()

    return success_response(
        data=None,
        message="Membre supprimé avec succès",
    )
