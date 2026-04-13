from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.members.schemas import (
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
    """
    Retourne le profil complet du membre connecté.
    Inclut : nom, email, téléphone, rôle, statut, photo de profil.
    """
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
    """
    Modifie le profil du membre connecté.
    Seuls les champs envoyés dans le body sont modifiés (PATCH partiel).

    Champs modifiables :
      - full_name    : nom complet
      - email        : adresse email (unicité vérifiée)
      - phone_number : numéro de téléphone (unicité vérifiée)
    """
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
    """
    Upload une photo de profil vers AWS S3.

    Formats acceptés : JPG, JPEG, PNG, WebP
    Taille maximum   : 5 Mo

    La photo est stockée dans S3 sous la clé :
      profiles/{member_id}/{uuid}.{ext}

    L'ancienne photo est automatiquement supprimée de S3 si elle existe.
    Retourne le profil mis à jour avec la nouvelle URL de la photo.
    """
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
# Routes dynamiques /{member_id} — TOUJOURS EN DERNIER
# ─────────────────────────────────────────────────────────────────────────────

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