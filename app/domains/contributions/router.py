from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.contributions.schemas import (
    TreasurerAddContributionRequest,
    UpdateAssociationSettingsRequest,
)
from app.domains.contributions.service import ContributionService
from app.infrastructure.database.session import get_db
from app.infrastructure.security.permissions import require_admin, require_treasurer
from app.shared.response import success_response

router = APIRouter()


@router.post("/declare")
async def declare_contribution(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
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
    now = datetime.now(UTC)
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


@router.get("/settings", dependencies=[Depends(require_treasurer)])
async def get_settings(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ContributionService(db)
    settings = await service.get_settings()

    return success_response(data=settings.model_dump())


@router.patch("/settings", dependencies=[Depends(require_treasurer)])
async def update_settings(
    data: UpdateAssociationSettingsRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
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


# ─────────────────────────────────────────────────────────────────────────────
# POST /contributions — Enregistrement d'une cotisation (comptable uniquement)
# ─────────────────────────────────────────────────────────────────────────────


@router.post("", dependencies=[Depends(require_treasurer)])
async def add_contribution(
    data: TreasurerAddContributionRequest,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ContributionService(db)
    contribution = await service.add_contribution_by_treasurer(
        data=data,
        treasurer=current_member,
    )
    await db.commit()

    return success_response(
        data=contribution.model_dump(),
        message=(
            f"Cotisation de {contribution.member_name} enregistrée "
            f"pour {contribution.contribution_month:02d}/{contribution.contribution_year}"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /contributions/me — Mes cotisations
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/me")
async def get_my_contributions(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    service = ContributionService(db)
    contributions = await service.get_my_contributions(current_member)

    return success_response(
        data=[c.model_dump() for c in contributions],
        message=f"{len(contributions)} cotisation(s) trouvée(s)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /contributions — Toutes les cotisations (avec filtres)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("")
async def list_contributions(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    member_id: str | None = Query(
        default=None,
        description="Filtrer par membre (UUID)",
    ),
    month: int | None = Query(
        default=None,
        ge=1,
        le=12,
        description="Filtrer par mois (1-12)",
    ),
    year: int | None = Query(
        default=None,
        ge=2020,
        le=2100,
        description="Filtrer par année (ex: 2026)",
    ),
):
    service = ContributionService(db)
    contributions = await service.list_contributions(
        member_id=member_id,
        month=month,
        year=year,
    )

    return success_response(
        data=[c.model_dump() for c in contributions],
        message=f"{len(contributions)} cotisation(s) trouvée(s)",
    )
