from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.contributions.schemas import (
    AssociationSettingsResponse,
    ContributionResponse,
    ContributionStatusResponse,
    DeclareContributionResponse,
    MonthlyReportResponse,
    TreasurerAddContributionRequest,
    UpdateAssociationSettingsRequest,
)
from app.infrastructure.database.models import (
    AssociationSettings,
    Contribution,
    Member,
    Transaction,
    TreasuryBalance,
)
from app.shared.enums import ContributionStatus, MemberStatus
from app.shared.exceptions import BusinessRuleError, ConflictError, NotFoundError


class ContributionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # SETTINGS DE L'ASSOCIATION
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_or_create_settings(self) -> AssociationSettings:
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
        asso_settings = await self._get_or_create_settings()

        updated_by_name = None
        if asso_settings.updated_by:
            result = await self._db.execute(
                select(Member.full_name).where(Member.id == asso_settings.updated_by)
            )
            updated_by_name = result.scalar_one_or_none()

        return AssociationSettingsResponse(
            contribution_mode=asso_settings.contribution_mode,
            fixed_amount=(
                float(asso_settings.fixed_amount) if asso_settings.fixed_amount else None
            ),
            currency=asso_settings.currency,
            updated_at=(asso_settings.updated_at.isoformat() if asso_settings.updated_at else None),
            updated_by_name=updated_by_name,
        )

    async def update_settings(
        self,
        data: UpdateAssociationSettingsRequest,
        updated_by: Member,
    ) -> AssociationSettingsResponse:
        asso_settings = await self._get_or_create_settings()

        asso_settings.contribution_mode = data.contribution_mode
        asso_settings.fixed_amount = data.fixed_amount
        asso_settings.updated_by = updated_by.id
        asso_settings.updated_at = datetime.now(UTC)

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
        now = datetime.now(UTC)
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
                    if asso_settings.contribution_mode == "fixed" and asso_settings.fixed_amount
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
                    if asso_settings.contribution_mode == "fixed" and asso_settings.fixed_amount
                    else None
                ),
                contribution_mode=asso_settings.contribution_mode,
                message=("Votre déclaration est en attente de confirmation par le comptable."),
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
                if asso_settings.contribution_mode == "fixed" and asso_settings.fixed_amount
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
            select(Member).where(Member.status == MemberStatus.ACTIVE).order_by(Member.full_name)
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
        contribs_by_member = {c.member_id: c for c in contribs_result.scalars().all()}

        # Construit la liste de statuts
        member_statuses = []
        total_collected = 0.0

        for m in members:
            contrib = contribs_by_member.get(m.id)

            if contrib:
                status = contrib.status
                amount = float(contrib.amount) if contrib.amount else None
                expected = float(contrib.expected_amount) if contrib.expected_amount else None
                mode = contrib.contribution_mode
                declared_at = contrib.declared_at.isoformat() if contrib.declared_at else None
                confirmed_at = contrib.confirmed_at.isoformat() if contrib.confirmed_at else None
                is_complete = contrib.is_complete
                if status == ContributionStatus.CONFIRMED and amount:
                    total_collected += amount
            else:
                status = ContributionStatus.PENDING
                amount = None
                expected = (
                    float(asso_settings.fixed_amount)
                    if asso_settings.contribution_mode == "fixed" and asso_settings.fixed_amount
                    else None
                )
                mode = asso_settings.contribution_mode
                declared_at = None
                confirmed_at = None
                is_complete = False

            member_statuses.append(
                ContributionStatusResponse(
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
                )
            )

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
                float(asso_settings.fixed_amount) if asso_settings.fixed_amount else None
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

    async def add_contribution_by_treasurer(
        self,
        data: TreasurerAddContributionRequest,
        treasurer: Member,
    ) -> ContributionResponse:
        now = datetime.now(UTC)

        # Mois et année : courants par défaut, ou saisis par le comptable
        month = data.contribution_month or now.month
        year = data.contribution_year or now.year

        # ── 1. Charge le membre ─────────────────────────────────────────────
        member_result = await self._db.execute(select(Member).where(Member.id == data.member_id))
        target_member = member_result.scalar_one_or_none()
        if not target_member:
            raise NotFoundError("Membre introuvable")

        # ── 2. Charge les settings ──────────────────────────────────────────
        settings_result = await self._db.execute(select(AssociationSettings))
        settings = settings_result.scalar_one_or_none()
        if not settings:
            raise BusinessRuleError(
                "Les paramètres de l'association ne sont pas configurés. "
                "Contactez l'administrateur."
            )

        contribution_mode = settings.contribution_mode  # "free" ou "fixed"

        # ── 3. Détermine le montant selon le mode ───────────────────────────
        if contribution_mode == "free":
            if data.amount is None:
                raise BusinessRuleError("Le montant est obligatoire en mode de cotisation libre")
            amount = data.amount
            expected_amount = None
        else:
            # Mode fixed : montant défini dans les settings
            if not settings.fixed_amount:
                raise BusinessRuleError(
                    "Le montant fixe n'est pas configuré dans les paramètres. "
                    "Contactez l'administrateur."
                )
            amount = float(settings.fixed_amount)
            expected_amount = float(settings.fixed_amount)

        # ── 4. Vérifie l'unicité (un seul cotisation par membre/mois/année) ─
        existing_result = await self._db.execute(
            select(Contribution).where(
                Contribution.member_id == data.member_id,
                Contribution.contribution_month == month,
                Contribution.contribution_year == year,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            raise ConflictError(
                f"{target_member.full_name} a déjà une cotisation enregistrée "
                f"pour {month:02d}/{year}"
            )

        # ── 5. Charge le solde de la caisse ────────────────────────────────
        balance_result = await self._db.execute(select(TreasuryBalance))
        treasury = balance_result.scalar_one_or_none()
        if not treasury:
            raise BusinessRuleError(
                "La caisse n'a pas encore été initialisée. Veuillez d'abord initialiser la caisse."
            )

        new_balance = float(treasury.balance) + amount

        # ── 6. Crée la transaction ──────────────────────────────────────────
        transaction = Transaction(
            amount=amount,
            balance_after=new_balance,
            member_id=target_member.id,
            performed_by=treasurer.id,
            status="confirmed",
            description=(f"Cotisation {target_member.full_name} — {month:02d}/{year}"),
            performed_at=now,
        )
        transaction.type = "deposit"
        self._db.add(transaction)
        await self._db.flush()  # Génère transaction.id

        # ── 7. Crée la cotisation ───────────────────────────────────────────
        contribution = Contribution(
            member_id=target_member.id,
            transaction_id=transaction.id,
            contribution_month=month,
            contribution_year=year,
            status="confirmed",
            amount=amount,
            expected_amount=expected_amount,
            contribution_mode=contribution_mode,
            declared_at=now,  # Déclaré et confirmé en même temps
            confirmed_at=now,  # Immédiatement confirmé
            recorded_at=now,  # Horodatage exact d'enregistrement
            recorded_by=treasurer.id,
        )
        self._db.add(contribution)

        # ── 8. Met à jour le solde de la caisse ─────────────────────────────
        treasury.balance = new_balance

        await self._db.flush()

        return ContributionResponse.from_model(
            contribution,
            member_name=target_member.full_name,
            recorded_by_name=treasurer.full_name,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LIST CONTRIBUTIONS — Historique
    # ─────────────────────────────────────────────────────────────────────────

    async def list_contributions(
        self,
        member_id: str | None = None,
        month: int | None = None,
        year: int | None = None,
    ) -> list[ContributionResponse]:
        """
        Liste les cotisations avec filtres optionnels.
        Chargement des noms de membre et comptable via jointures.
        """
        from sqlalchemy.orm import joinedload

        query = select(Contribution).options(
            joinedload(Contribution.member),
            joinedload(Contribution.recorder),
        )

        if member_id:
            query = query.where(Contribution.member_id == member_id)
        if month:
            query = query.where(Contribution.contribution_month == month)
        if year:
            query = query.where(Contribution.contribution_year == year)

        query = query.order_by(
            Contribution.contribution_year.desc(),
            Contribution.contribution_month.desc(),
        )

        result = await self._db.execute(query)
        contributions = result.unique().scalars().all()

        return [
            ContributionResponse.from_model(
                c,
                member_name=c.member.full_name if c.member else "",
                recorded_by_name=c.recorder.full_name if c.recorder else None,
            )
            for c in contributions
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # GET MY CONTRIBUTIONS — Cotisations du membre connecté
    # ─────────────────────────────────────────────────────────────────────────

    async def get_my_contributions(self, member: Member) -> list[ContributionResponse]:
        return await self.list_contributions(member_id=str(member.id))
