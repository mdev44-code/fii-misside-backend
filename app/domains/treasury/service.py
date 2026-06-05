import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.service import NotificationService
from app.domains.treasury.schemas import (
    BalanceResponse,
    ConfirmDepositRequest,
    ExpenseRequest,
    TransactionResponse,
)
from app.infrastructure.database.models import (
    AuditLog,
    Contribution,
    Member,
    Project,
    Transaction,
    TreasuryBalance,
)
from app.shared.enums import (
    AuditAction,
    ContributionStatus,
    ProjectStatus,
    TransactionStatus,
    TransactionType,
)
from app.shared.exceptions import (
    BusinessRuleError,
    NotFoundError,
)

# Statuts qui interdisent toute nouvelle dépense sur un projet
_TERMINAL_PROJECT_STATUSES = {
    ProjectStatus.COMPLETED,
    ProjectStatus.ABANDONED,
    ProjectStatus.CANCELLED,
}

# Labels lisibles pour les messages d'erreur
_STATUS_LABELS: dict[ProjectStatus, str] = {
    ProjectStatus.DRAFT: "Brouillon",
    ProjectStatus.IN_PROGRESS: "En cours",
    ProjectStatus.COMPLETED: "Terminé",
    ProjectStatus.SUSPENDED: "Suspendu",
    ProjectStatus.ABANDONED: "Abandonné",
    ProjectStatus.CANCELLED: "Annulé",
}


class TreasuryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS — Balance
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_or_create_balance(self) -> TreasuryBalance:
        result = await self._db.execute(select(TreasuryBalance))
        balance = result.scalar_one_or_none()

        if not balance:
            balance = TreasuryBalance(balance=0, initial_balance=0)
            self._db.add(balance)
            await self._db.flush()  # génère l'UUID sans committer

        return balance

    # ─────────────────────────────────────────────────────────────────────────
    # INITIALISATION
    # ─────────────────────────────────────────────────────────────────────────

    async def initialize_balance(
        self,
        initial_balance: float,
        treasurer: Member,
    ) -> BalanceResponse:
        balance = await self._get_or_create_balance()

        old_balance = float(balance.balance)
        balance.balance = initial_balance
        balance.initial_balance = initial_balance
        balance.initialized_by = treasurer.id
        balance.initialized_at = datetime.now(UTC)

        # Trace l'initialisation dans l'historique des transactions
        tx = Transaction(
            type=TransactionType.ADJUSTMENT,
            amount=initial_balance,
            balance_after=initial_balance,
            performed_by=treasurer.id,
            status=TransactionStatus.CONFIRMED,
            description=f"Initialisation de la caisse : {initial_balance:,.0f} FCFA",
            performed_at=datetime.now(UTC),
        )
        self._db.add(tx)

        # Audit log avec ancien et nouveau solde
        log = AuditLog(
            member_id=treasurer.id,
            action=AuditAction.UPDATE,
            entity_type="treasury_balance",
            entity_id=balance.id,
            old_values={"balance": old_balance},
            new_values={"balance": initial_balance},
        )
        self._db.add(log)

        await self._db.flush()

        return BalanceResponse(
            balance=float(balance.balance),
            initial_balance=float(balance.initial_balance),
            initialized_at=balance.initialized_at.isoformat(),
            initialized_by_name=treasurer.full_name,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # DÉPÔT (confirmation cotisation)
    # ─────────────────────────────────────────────────────────────────────────

    async def confirm_deposit(
        self,
        data: ConfirmDepositRequest,
        treasurer: Member,
    ) -> TransactionResponse:
        # 1. Vérifie que le membre cotisant existe
        member_result = await self._db.execute(
            select(Member).where(Member.id == uuid.UUID(data.member_id))
        )
        member = member_result.scalar_one_or_none()
        if not member:
            raise NotFoundError("Membre", data.member_id)

        # 2. Récupère la caisse
        balance = await self._get_or_create_balance()
        current_balance = float(balance.balance)
        new_balance = current_balance + data.amount

        # 3. Crée la transaction
        tx = Transaction(
            type=TransactionType.DEPOSIT,
            amount=data.amount,
            balance_after=new_balance,
            member_id=uuid.UUID(data.member_id),
            performed_by=treasurer.id,
            status=TransactionStatus.CONFIRMED,
            description=data.description
            or (f"Cotisation de {member.full_name} — {data.amount:,.0f} FCFA"),
            performed_at=datetime.now(UTC),
        )
        self._db.add(tx)

        # 4. Met à jour le solde
        balance.balance = new_balance

        await self._db.flush()

        # 5. Confirme la contribution du mois en cours
        await self._confirm_contribution(
            member_id=data.member_id,
            transaction_id=tx.id,
            amount=data.amount,
            contribution_id=data.contribution_id,
        )

        notification_service = NotificationService(self._db)
        await notification_service.send_contribution_received_broadcast(
            member_name=member.full_name,
            amount=data.amount,
            triggered_by_id=str(treasurer.id),
        )

        # 7. Audit log
        self._db.add(
            AuditLog(
                member_id=treasurer.id,
                action=AuditAction.CONFIRM,
                entity_type="transaction",
                entity_id=tx.id,
                new_values={
                    "amount": data.amount,
                    "member_id": data.member_id,
                    "balance_after": new_balance,
                },
            )
        )

        return TransactionResponse.from_model(
            tx,
            member_name=member.full_name,
            performed_by_name=treasurer.full_name,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # DÉPENSE
    # ─────────────────────────────────────────────────────────────────────────

    async def record_expense(
        self,
        data: ExpenseRequest,
        treasurer: Member,
    ) -> TransactionResponse:
        balance = await self._get_or_create_balance()
        current_balance = float(balance.balance)

        if data.amount > current_balance:
            raise BusinessRuleError(
                f"Solde insuffisant. Solde actuel : {current_balance:,.0f} FCFA, "
                f"dépense demandée : {data.amount:,.0f} FCFA."
            )

        new_balance = current_balance - data.amount

        tx = Transaction(
            type=TransactionType.EXPENSE,
            amount=data.amount,
            balance_after=new_balance,
            performed_by=treasurer.id,
            status=TransactionStatus.CONFIRMED,
            description=data.description,
            project_id=uuid.UUID(data.project_id) if data.project_id else None,
            performed_at=datetime.now(UTC),
        )
        self._db.add(tx)

        balance.balance = new_balance

        await self._db.flush()

        # Met à jour le budget_spent du projet si lié
        if data.project_id:
            project_result = await self._db.execute(
                select(Project).where(Project.id == uuid.UUID(data.project_id))
            )
            project = project_result.scalar_one_or_none()
            if project:
                project.budget_spent = float(project.budget_spent or 0) + data.amount

        notification_service = NotificationService(self._db)
        await notification_service.send_expense_broadcast(
            description=data.description,
            amount=data.amount,
            triggered_by_id=str(treasurer.id),
        )

        self._db.add(
            AuditLog(
                member_id=treasurer.id,
                action=AuditAction.CREATE,
                entity_type="transaction",
                entity_id=tx.id,
                new_values={
                    "amount": data.amount,
                    "description": data.description,
                    "balance_after": new_balance,
                    "project_id": data.project_id,
                },
            )
        )

        return TransactionResponse.from_model(
            tx,
            performed_by_name=treasurer.full_name,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE
    # ─────────────────────────────────────────────────────────────────────────

    async def get_balance(self) -> BalanceResponse:
        """Retourne le solde actuel de la caisse."""
        balance = await self._get_or_create_balance()

        # Charge le nom du comptable qui a initialisé si disponible
        initialized_by_name = None
        if balance.initialized_by:
            result = await self._db.execute(
                select(Member).where(Member.id == balance.initialized_by)
            )
            initializer = result.scalar_one_or_none()
            if initializer:
                initialized_by_name = initializer.full_name

        return BalanceResponse(
            balance=float(balance.balance),
            initial_balance=float(balance.initial_balance),
            initialized_at=(balance.initialized_at.isoformat() if balance.initialized_at else None),
            initialized_by_name=initialized_by_name,
        )

    async def get_transactions(
        self,
        limit: int = 50,
        offset: int = 0,
        transaction_type: str | None = None,
    ) -> tuple[list[TransactionResponse], int]:
        from sqlalchemy import func

        # Requête principale
        stmt = select(Transaction)
        count_stmt = select(func.count(Transaction.id))

        if transaction_type:
            stmt = stmt.where(Transaction.type == transaction_type)
            count_stmt = count_stmt.where(Transaction.type == transaction_type)

        # Plus récent en premier
        stmt = stmt.order_by(Transaction.performed_at.desc()).limit(limit).offset(offset)

        result = await self._db.execute(stmt)
        transactions = result.scalars().all()

        count_result = await self._db.execute(count_stmt)
        total = count_result.scalar_one()

        # Convertit en responses
        # Pour simplifier, on charge les noms séparément
        # (évite une jointure complexe pour ce volume de données)
        responses = []
        for tx in transactions:
            member_name = None
            if tx.member_id:
                m_result = await self._db.execute(
                    select(Member.full_name).where(Member.id == tx.member_id)
                )
                member_name = m_result.scalar_one_or_none()

            project_title = None
            if tx.project_id:
                p_result = await self._db.execute(
                    select(Project.title).where(Project.id == tx.project_id)
                )
                project_title = p_result.scalar_one_or_none()

            performer_result = await self._db.execute(
                select(Member.full_name).where(Member.id == tx.performed_by)
            )
            performed_by_name = performer_result.scalar_one_or_none() or ""

            responses.append(
                TransactionResponse.from_model(
                    tx,
                    member_name=member_name,
                    project_title=project_title,
                    performed_by_name=performed_by_name,
                )
            )

        return responses, total

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS
    # ─────────────────────────────────────────────────────────────────────────

    async def _validate_and_activate_project(
        self,
        project: Project,
        treasurer: Member,
    ) -> None:
        current_status = ProjectStatus(project.status)

        # Statuts terminaux : impossible de dépenser
        if current_status in _TERMINAL_PROJECT_STATUSES:
            status_label = _STATUS_LABELS.get(current_status, current_status.value)
            raise BusinessRuleError(
                f"Impossible d'enregistrer une dépense pour le projet '{project.title}' : "
                f"il est en statut '{status_label}'. "
                f"Un projet terminé, abandonné ou annulé ne peut plus recevoir de dépenses."
            )

        # Passage automatique de draft ou suspended → in_progress
        if current_status in (ProjectStatus.DRAFT, ProjectStatus.SUSPENDED):
            # Vérification : un projet draft sans budget ne peut pas démarrer
            if current_status == ProjectStatus.DRAFT and project.budget_allocated is None:
                raise BusinessRuleError(
                    f"Impossible d'enregistrer une dépense pour le projet '{project.title}' : "
                    f"il est en brouillon et n'a pas de budget défini. "
                    f"Ajoutez un budget au projet avant d'enregistrer des dépenses."
                )

            old_status = project.status
            project.status = ProjectStatus.IN_PROGRESS

            # Audit log pour tracer le changement automatique de statut
            self._db.add(
                AuditLog(
                    member_id=treasurer.id,
                    action=AuditAction.UPDATE,
                    entity_type="project",
                    entity_id=project.id,
                    old_values={"status": old_status},
                    new_values={
                        "status": ProjectStatus.IN_PROGRESS,
                        "auto_transition": True,
                        "reason": "Dépense enregistrée — passage automatique en cours",
                    },
                )
            )

    async def _confirm_contribution(
        self,
        member_id: str,
        transaction_id: uuid.UUID,
        amount: float,
        contribution_id: str | None,
    ) -> None:
        from app.infrastructure.database.models import AssociationSettings

        now = datetime.now(UTC)

        # Lit le mode de cotisation actuel depuis la BDD
        settings_result = await self._db.execute(select(AssociationSettings))
        asso_settings = settings_result.scalar_one_or_none()

        contribution_mode = "free"
        expected_amount = None

        if asso_settings:
            contribution_mode = asso_settings.contribution_mode
            if contribution_mode == "fixed" and asso_settings.fixed_amount:
                expected_amount = float(asso_settings.fixed_amount)

        # Cas 1 : contribution_id explicitement fourni
        if contribution_id:
            result = await self._db.execute(
                select(Contribution).where(Contribution.id == uuid.UUID(contribution_id))
            )
            contribution = result.scalar_one_or_none()
            if contribution:
                contribution.status = ContributionStatus.CONFIRMED
                contribution.transaction_id = transaction_id
                contribution.amount = amount
                contribution.expected_amount = expected_amount
                contribution.contribution_mode = contribution_mode
                contribution.confirmed_at = now
                return

        # Cas 2 : cherche une contribution DECLARED ce mois-ci
        result = await self._db.execute(
            select(Contribution).where(
                Contribution.member_id == uuid.UUID(member_id),
                Contribution.contribution_month == now.month,
                Contribution.contribution_year == now.year,
            )
        )
        contribution = result.scalar_one_or_none()

        if contribution:
            contribution.status = ContributionStatus.CONFIRMED
            contribution.transaction_id = transaction_id
            contribution.amount = amount
            contribution.expected_amount = expected_amount
            contribution.contribution_mode = contribution_mode
            contribution.confirmed_at = now
        else:
            # Cas 3 : crée directement une contribution confirmée
            new_contribution = Contribution(
                member_id=uuid.UUID(member_id),
                transaction_id=transaction_id,
                contribution_month=now.month,
                contribution_year=now.year,
                status=ContributionStatus.CONFIRMED,
                amount=amount,
                expected_amount=expected_amount,
                contribution_mode=contribution_mode,
                confirmed_at=now,
            )
            self._db.add(new_contribution)
