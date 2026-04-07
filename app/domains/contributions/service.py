"""
contributions/service.py — Logique métier des cotisations.

Ce service gère :
  1. La déclaration d'une cotisation par un membre
  2. Le rapport mensuel (qui a payé, qui n'a pas payé)
  3. La liste des membres sans cotisation (utilisée par le cron job)
  4. La gestion des paramètres de l'association (mode libre/fixe)

Relation avec treasury/service.py :
  Le service treasury confirme les cotisations (appelle _confirm_contribution).
  Ce service les déclare et les consulte.
  Les deux services ne s'appellent pas mutuellement — ils opèrent
  sur les mêmes données BDD mais depuis des angles différents.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.contributions.schemas import (
    AssociationSettingsResponse,
    ContributionStatusResponse,
    DeclareContributionResponse,
    MonthlyReportResponse,
    UpdateAssociationSettingsRequest,
)
from app.infrastructure.database.models import (
    AssociationSettings,
    Contribution,
    Member,
)
from app.shared.enums import ContributionStatus, MemberStatus


class ContributionService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # SETTINGS DE L'ASSOCIATION
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_or_create_settings(self) -> AssociationSettings:
        """
        Récupère les paramètres de l'association (singleton).
        Les crée avec les valeurs par défaut du .env si inexistants.
        """
        result = await self._db.execute(select(AssociationSettings))
        asso_settings = result.scalar_one_or_none()

        if not asso_settings:
            asso_settings = AssociationSettings(
                contribution_mode=settings.contribution_mode,
                fixed_amount=(
                    settings.contribution_fixed_amount
                    if settings.contribution_mode == "fixed"
                    else None
                ),
                currency="XOF",
            )
            self._db.add(asso_settings)
            await self._db.flush()

        return asso_settings

    async def get_settings(self) -> AssociationSettingsResponse:
        """Retourne les paramètres actuels de l'association."""
        asso_settings = await self._get_or_create_settings()

        updated_by_name = None
        if asso_settings.updated_by:
            result = await self._db.execute(
                select(Member.full_name).where(
                    Member.id == asso_settings.updated_by
                )
            )
            updated_by_name = result.scalar_one_or_none()

        return AssociationSettingsResponse(
            contribution_mode=asso_settings.contribution_mode,
            fixed_amount=(
                float(asso_settings.fixed_amount)
                if asso_settings.fixed_amount else None
            ),
            currency=asso_settings.currency,
            updated_at=(
                asso_settings.updated_at.isoformat()
                if asso_settings.updated_at else None
            ),
            updated_by_name=updated_by_name,
        )

    async def update_settings(
        self,
        data: UpdateAssociationSettingsRequest,
        updated_by: Member,
    ) -> AssociationSettingsResponse:
        """
        Met à jour le mode de cotisation.
        Seul l'admin peut faire ça (vérifié dans le router).

        Exemple : passer de "free" à "fixed" à 5000 FCFA.
        Les cotisations existantes ne sont pas modifiées (snapshot préservé).
        """
        asso_settings = await self._get_or_create_settings()

        asso_settings.contribution_mode = data.contribution_mode
        asso_settings.fixed_amount = data.fixed_amount
        asso_settings.updated_by = updated_by.id
        asso_settings.updated_at = datetime.now(timezone.utc)

        return await self.get_settings()

    # ─────────────────────────────────────────────────────────────────────────
    # DÉCLARATION DE COTISATION
    # ─────────────────────────────────────────────────────────────────────────

    async def declare_contribution(
        self,
        member: Member,
    ) -> DeclareContributionResponse:
        """
        Le membre déclare qu'il a envoyé sa cotisation Wave.

        Logique :
          - Si une contribution CONFIRMED existe ce mois → informe le membre
          - Si une contribution DECLARED existe → retourne l'existante (idempotent)
          - Sinon → crée une nouvelle contribution en statut DECLARED

        Idempotent : appeler deux fois ne crée pas deux contributions.
        """
        now = datetime.now(timezone.utc)
        asso_settings = await self._get_or_create_settings()

        # Cherche une contribution existante ce mois
        result = await self._db.execute(
            select(Contribution).where(
                Contribution.member_id == member.id,
                Contribution.contribution_month == now.month,
                Contribution.contribution_year == now.year,
            )
        )
        existing = result.scalar_one_or_none()

        # Déjà confirmée → informe le membre
        if existing and existing.status == ContributionStatus.CONFIRMED:
            return DeclareContributionResponse(
                contribution_id=str(existing.id),
                status=existing.status,
                wave_number=settings.wave_treasurer_number,
                amount_to_send=(
                    float(asso_settings.fixed_amount)
                    if asso_settings.contribution_mode == "fixed"
                    and asso_settings.fixed_amount
                    else None
                ),
                contribution_mode=asso_settings.contribution_mode,
                message="Votre cotisation de ce mois est déjà confirmée. Merci !",
            )

        # Déjà déclarée → retourne l'existante (idempotent)
        if existing and existing.status == ContributionStatus.DECLARED:
            return DeclareContributionResponse(
                contribution_id=str(existing.id),
                status=existing.status,
                wave_number=settings.wave_treasurer_number,
                amount_to_send=(
                    float(asso_settings.fixed_amount)
                    if asso_settings.contribution_mode == "fixed"
                    and asso_settings.fixed_amount
                    else None
                ),
                contribution_mode=asso_settings.contribution_mode,
                message=(
                    "Votre déclaration est en attente de confirmation "
                    "par le comptable."
                ),
            )

        # Crée une nouvelle contribution
        contribution = Contribution(
            member_id=member.id,
            contribution_month=now.month,
            contribution_year=now.year,
            status=ContributionStatus.DECLARED,
            contribution_mode=asso_settings.contribution_mode,
            declared_at=now,
        )
        self._db.add(contribution)
        await self._db.flush()

        # Message selon le mode
        if asso_settings.contribution_mode == "fixed" and asso_settings.fixed_amount:
            amount = float(asso_settings.fixed_amount)
            message = (
                f"Envoyez {amount:,.0f} FCFA via Wave au "
                f"{settings.wave_treasurer_number}, "
                f"puis informez le comptable pour confirmation."
            )
        else:
            message = (
                f"Envoyez votre cotisation via Wave au "
                f"{settings.wave_treasurer_number}, "
                f"puis informez le comptable du montant envoyé."
            )

        return DeclareContributionResponse(
            contribution_id=str(contribution.id),
            status=contribution.status,
            wave_number=settings.wave_treasurer_number,
            amount_to_send=(
                float(asso_settings.fixed_amount)
                if asso_settings.contribution_mode == "fixed"
                and asso_settings.fixed_amount
                else None
            ),
            contribution_mode=asso_settings.contribution_mode,
            message=message,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # RAPPORT MENSUEL
    # ─────────────────────────────────────────────────────────────────────────

    async def get_monthly_report(
        self,
        month: int,
        year: int,
    ) -> MonthlyReportResponse:
        """
        Rapport complet des cotisations pour un mois donné.

        Charge tous les membres actifs et leur statut de cotisation.
        Calcule les statistiques globales (combien ont payé, total collecté...).
        """
        asso_settings = await self._get_or_create_settings()

        # Charge tous les membres actifs
        members_result = await self._db.execute(
            select(Member)
            .where(Member.status == MemberStatus.ACTIVE)
            .order_by(Member.full_name)
        )
        members = members_result.scalars().all()

        # Charge toutes les contributions du mois en une seule requête
        # (évite N+1 queries — une requête pour tous, pas une par membre)
        contribs_result = await self._db.execute(
            select(Contribution).where(
                Contribution.contribution_month == month,
                Contribution.contribution_year == year,
            )
        )
        # Indexe par member_id pour un accès O(1)
        contribs_by_member = {
            c.member_id: c for c in contribs_result.scalars().all()
        }

        # Construit la liste de statuts
        member_statuses = []
        total_collected = 0.0

        for m in members:
            contrib = contribs_by_member.get(m.id)

            if contrib:
                status = contrib.status
                amount = float(contrib.amount) if contrib.amount else None
                expected = (
                    float(contrib.expected_amount)
                    if contrib.expected_amount else None
                )
                mode = contrib.contribution_mode
                declared_at = (
                    contrib.declared_at.isoformat()
                    if contrib.declared_at else None
                )
                confirmed_at = (
                    contrib.confirmed_at.isoformat()
                    if contrib.confirmed_at else None
                )
                is_complete = contrib.is_complete
                if status == ContributionStatus.CONFIRMED and amount:
                    total_collected += amount
            else:
                status = ContributionStatus.PENDING
                amount = None
                expected = (
                    float(asso_settings.fixed_amount)
                    if asso_settings.contribution_mode == "fixed"
                    and asso_settings.fixed_amount
                    else None
                )
                mode = asso_settings.contribution_mode
                declared_at = None
                confirmed_at = None
                is_complete = False

            member_statuses.append(ContributionStatusResponse(
                member_id=str(m.id),
                full_name=m.full_name,
                phone_number=m.phone_number,
                status=status,
                amount=amount,
                expected_amount=expected,
                contribution_mode=mode,
                declared_at=declared_at,
                confirmed_at=confirmed_at,
                is_complete=is_complete,
            ))

        # Calcule les statistiques
        confirmed = sum(1 for s in member_statuses if s.status == ContributionStatus.CONFIRMED)
        declared = sum(1 for s in member_statuses if s.status == ContributionStatus.DECLARED)
        pending = sum(1 for s in member_statuses if s.status == ContributionStatus.PENDING)
        late = sum(1 for s in member_statuses if s.status == ContributionStatus.LATE)

        return MonthlyReportResponse(
            month=month,
            year=year,
            contribution_mode=asso_settings.contribution_mode,
            fixed_amount=(
                float(asso_settings.fixed_amount)
                if asso_settings.fixed_amount else None
            ),
            members=member_statuses,
            total_members=len(members),
            confirmed_count=confirmed,
            declared_count=declared,
            pending_count=pending,
            late_count=late,
            total_collected=total_collected,
        )

    async def get_members_without_contribution(
        self,
        month: int,
        year: int,
    ) -> list[Member]:
        """
        Retourne les membres actifs sans cotisation confirmée ce mois.
        Utilisé par le cron job pour envoyer les rappels ciblés.

        Sous-requête SQL :
          Récupère les IDs des membres ayant une contribution CONFIRMED ce mois.
          Puis sélectionne les membres actifs qui NE SONT PAS dans cette liste.
        """
        from sqlalchemy import func

        # IDs des membres qui ont déjà cotisé ce mois
        confirmed_ids_subquery = (
            select(Contribution.member_id)
            .where(
                Contribution.contribution_month == month,
                Contribution.contribution_year == year,
                Contribution.status == ContributionStatus.CONFIRMED,
            )
            .scalar_subquery()
        )

        # Membres actifs qui ne sont PAS dans la sous-requête
        result = await self._db.execute(
            select(Member).where(
                Member.status == MemberStatus.ACTIVE,
                Member.id.not_in(confirmed_ids_subquery),
            )
        )
        return list(result.scalars().all())