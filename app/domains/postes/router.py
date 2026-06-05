from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.postes.schemas import (
    AssignPosteRequest,
    CreatePosteRequest,
    UpdatePosteRequest,
)
from app.domains.postes.service import PosteService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_admin
from app.shared.response import success_response

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# GET /postes — Organigramme complet
# ─────────────────────────────────────────────────────────────────────────────


@router.get("")
async def list_postes(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne tous les postes de l'organigramme avec leurs titulaires.
    Accessible à tous les membres connectés.
    """
    service = PosteService(db)
    chart = await service.get_all_postes()

    return success_response(
        data=chart.model_dump(),
        message=f"{chart.total} poste(s) — {chart.occupied} occupé(s), {chart.vacant} vacant(s)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /postes — Créer un poste
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", dependencies=[Depends(require_admin)])
async def create_poste(
    data: CreatePosteRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Crée un nouveau poste dans l'organigramme.
    Réservé aux administrateurs.

    Le poste peut être créé vacant ou directement attribué à un membre.
    """
    service = PosteService(db)
    poste = await service.create_poste(data)
    await db.commit()

    return success_response(
        data=poste.model_dump(),
        message=f"Poste '{poste.title}' créé avec succès",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /postes/{id} — Détail d'un poste
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/{poste_id}")
async def get_poste(
    poste_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne les détails d'un poste par son ID.
    Accessible à tous les membres connectés.
    """
    service = PosteService(db)
    poste = await service.get_poste_by_id(poste_id)

    return success_response(data=poste.model_dump())


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /postes/{id} — Modifier un poste
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{poste_id}", dependencies=[Depends(require_admin)])
async def update_poste(
    poste_id: str,
    data: UpdatePosteRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Modifie le titre d'un poste.
    Réservé aux administrateurs.
    """
    service = PosteService(db)
    poste = await service.update_poste(poste_id, data)
    await db.commit()

    return success_response(
        data=poste.model_dump(),
        message=f"Poste '{poste.title}' mis à jour",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /postes/{id}/assign — Attribuer/libérer un poste
# ─────────────────────────────────────────────────────────────────────────────


@router.patch("/{poste_id}/assign", dependencies=[Depends(require_admin)])
async def assign_poste(
    poste_id: str,
    data: AssignPosteRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Attribue un membre à un poste ou libère le poste.
    Réservé aux administrateurs.

    Envoyer member_id = null pour libérer le poste.

    Si le poste est déjà occupé, l'ancien titulaire est automatiquement
    remplacé par le nouveau membre.

    Règles métier :
      - Le membre doit être actif
      - Le membre ne peut pas déjà occuper un autre poste
      - Un poste ne peut être occupé que par un seul membre
    """
    service = PosteService(db)
    poste = await service.assign_member(poste_id, data)
    await db.commit()

    if poste.is_vacant:
        message = f"Poste '{poste.title}' libéré"
    else:
        message = f"Poste '{poste.title}' attribué à {poste.member.full_name}"

    return success_response(
        data=poste.model_dump(),
        message=message,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /postes/{id} — Supprimer un poste
# ─────────────────────────────────────────────────────────────────────────────


@router.delete("/{poste_id}", dependencies=[Depends(require_admin)])
async def delete_poste(
    poste_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Supprime un poste de l'organigramme.
    Réservé aux administrateurs.

    Le membre affecté n'est pas supprimé — il perd juste son poste.
    """
    service = PosteService(db)
    await service.delete_poste(poste_id)
    await db.commit()

    return success_response(message="Poste supprimé avec succès")
