"""
contributions/router.py — Endpoints HTTP pour les cotisations.

Routes :
  POST /contributions/declare           → membre (déclare sa cotisation)
  GET  /contributions/status            → tous (rapport du mois courant)
  GET  /contributions/status?month=&year= → tous (rapport d'un mois précis)
  GET  /contributions/settings          → tous (voir le mode actuel)
  PATCH /contributions/settings         → admin uniquement
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.contributions.schemas import UpdateAssociationSettingsRequest
from app.domains.contributions.service import ContributionService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_admin
from app.shared.response import success_response

router = APIRouter()


@router.post("/declare")
async def declare_contribution(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Le membre déclare qu'il a envoyé sa cotisation via Wave.

    Retourne le numéro Wave du comptable et le montant à envoyer
    (si mode fixe). Le membre peut appeler cette route plusieurs fois —
    le résultat est idempotent (pas de doublon créé).
    """
    service = ContributionService(db)
    result = await service.declare_contribution(current_member)

    return success_response(
        data=result.model_dump(),
        message=result.message,
    )


@router.get("/status")
async def get_monthly_status(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = Query(default=None, ge=2020),
):
    """
    Rapport des cotisations pour un mois donné.
    Par défaut : mois courant.

    Accessible à tous les membres — transparence totale.

    Paramètres URL optionnels :
      ?month=6&year=2025 → rapport de juin 2025
      (sans paramètres)  → mois courant
    """
    now = datetime.now(timezone.utc)
    target_month = month or now.month
    target_year = year or now.year

    service = ContributionService(db)
    report = await service.get_monthly_report(target_month, target_year)

    return success_response(
        data=report.model_dump(),
        message=(
            f"Rapport de cotisation — "
            f"{target_month:02d}/{target_year} : "
            f"{report.confirmed_count}/{report.total_members} membres ont cotisé"
        ),
    )


@router.get("/settings")
async def get_settings(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne les paramètres actuels de cotisation.
    Accessible à tous les membres.
    """
    service = ContributionService(db)
    settings = await service.get_settings()

    return success_response(data=settings.model_dump())


@router.patch("/settings", dependencies=[Depends(require_admin)])
async def update_settings(
    data: UpdateAssociationSettingsRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Met à jour le mode de cotisation.
    Réservé aux administrateurs.

    Exemple : passer en mode fixe à 5 000 FCFA :
      PATCH /contributions/settings
      Body : { "contribution_mode": "fixed", "fixed_amount": 5000 }
    """
    service = ContributionService(db)
    result = await service.update_settings(data, updated_by=current_member)

    mode_label = "fixe" if data.contribution_mode == "fixed" else "libre"
    message = f"Mode de cotisation mis à jour : {mode_label}"
    if data.contribution_mode == "fixed" and data.fixed_amount:
        message += f" — {data.fixed_amount:,.0f} FCFA par membre"

    return success_response(
        data=result.model_dump(),
        message=message,
    )