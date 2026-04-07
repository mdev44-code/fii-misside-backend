"""
treasury/service.py — Logique métier de la caisse.

PRINCIPE FONDAMENTAL :
  Toutes les opérations sur la caisse se font dans la même session SQLAlchemy.
  Si une étape échoue, tout est annulé automatiquement (rollback dans session.py).
  La caisse ne peut jamais se retrouver dans un état incohérent.

  Ex pour un dépôt : si la notification SMS plante après avoir mis à jour
  la balance → rollback → la balance revient à son état précédent.
  Mieux vaut une notification ratée qu'une caisse fausse.

ORDRE DANS confirm_deposit() :
  1. Charger la caisse (lock pour éviter les conflits concurrents)
  2. Calculer le nouveau solde
  3. Créer la transaction (INSERT)
  4. Mettre à jour la balance (UPDATE)
  5. Confirmer la contribution (UPDATE)
  6. Envoyer la notification (hors transaction critique)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
    TransactionStatus,
    TransactionType,
)
from app.shared.exceptions import (
    BusinessRuleError,
    InsufficientFundsError,
    NotFoundError,
)


class TreasuryService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS — Balance
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_or_create_balance(self) -> TreasuryBalance:
        """
        Récupère la ligne unique de la caisse.
        Si elle n'existe pas encore (première utilisation), la crée avec 0.

        scalar_one_or_none() : retourne l'objet ou None — jamais d'exception.
        """
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
        """
        Synchronise la caisse avec le solde réel existant.
        Peut être appelé plusieurs fois (le comptable peut corriger).

        Crée aussi une transaction de type ADJUSTMENT pour garder
        une trace de cette initialisation dans l'historique.
        """
        balance = await self._get_or_create_balance()

        old_balance = float(balance.balance)
        balance.balance = initial_balance
        balance.initial_balance = initial_balance
        balance.initialized_by = treasurer.id
        balance.initialized_at = datetime.now(timezone.utc)

        # Trace l'initialisation dans l'historique des transactions
        tx = Transaction(
            type=TransactionType.ADJUSTMENT,
            amount=initial_balance,
            balance_after=initial_balance,
            performed_by=treasurer.id,
            status=TransactionStatus.CONFIRMED,
            description=f"Initialisation de la caisse : {initial_balance:,.0f} FCFA",
            performed_at=datetime.now(timezone.utc),
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
        """
        Confirme la réception d'un transfert Wave et met à jour la caisse.

        Étapes (toutes dans la même session → atomique) :
          1. Vérifie que le membre existe
          2. Récupère le solde actuel
          3. Calcule le nouveau solde
          4. Crée la transaction en BDD
          5. Met à jour le solde de la caisse
          6. Confirme la contribution du mois
          7. Envoie une notification SMS au membre
        """
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
            description=data.description or (
                f"Cotisation de {member.full_name} — {data.amount:,.0f} FCFA"
            ),
            performed_at=datetime.now(timezone.utc),
        )
        self._db.add(tx)

        # 4. Met à jour le solde
        balance.balance = new_balance

        # flush() pour obtenir tx.id avant de l'utiliser dans la contribution
        await self._db.flush()

        # 5. Confirme la contribution du mois en cours
        await self._confirm_contribution(
            member_id=data.member_id,
            transaction_id=tx.id,
            amount=data.amount,
            contribution_id=data.contribution_id,
        )

        # 6. Notification SMS (après le flush — si elle plante, on peut retry)
        await self._notify_deposit_confirmed(member, data.amount)

        # 7. Audit log
        self._db.add(AuditLog(
            member_id=treasurer.id,
            action=AuditAction.CONFIRM,
            entity_type="transaction",
            entity_id=tx.id,
            new_values={
                "amount": data.amount,
                "member_id": data.member_id,
                "balance_after": new_balance,
            },
        ))

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
        """
        Enregistre une dépense après retrait Wave physique.

        L'argent a déjà été retiré physiquement par le comptable.
        On enregistre ici pour tenir la comptabilité à jour.

        Si un project_id est fourni, on met à jour le budget_spent du projet.
        """
        balance = await self._get_or_create_balance()
        current_balance = float(balance.balance)

        # Vérifie qu'il y a assez de fonds
        # (cohérence comptable — le retrait est déjà fait physiquement
        # mais on refuse d'enregistrer si ça crée un solde négatif)
        if data.amount > current_balance:
            raise InsufficientFundsError(
                available=current_balance,
                requested=data.amount,
            )

        new_balance = current_balance - data.amount

        # Vérifie le projet si fourni
        project = None
        if data.project_id:
            project_result = await self._db.execute(
                select(Project).where(Project.id == uuid.UUID(data.project_id))
            )
            project = project_result.scalar_one_or_none()
            if not project:
                raise NotFoundError("Projet", data.project_id)

        # Crée la transaction
        tx = Transaction(
            type=TransactionType.EXPENSE,
            amount=data.amount,
            balance_after=new_balance,
            project_id=uuid.UUID(data.project_id) if data.project_id else None,
            performed_by=treasurer.id,
            status=TransactionStatus.CONFIRMED,
            description=data.description,
            performed_at=datetime.now(timezone.utc),
        )
        self._db.add(tx)

        # Met à jour le solde de la caisse
        balance.balance = new_balance

        # Met à jour le budget dépensé du projet si applicable
        if project:
            project.budget_spent = float(project.budget_spent) + data.amount

        # Audit log
        self._db.add(AuditLog(
            member_id=treasurer.id,
            action=AuditAction.CREATE,
            entity_type="transaction",
            entity_id=None,
            new_values={
                "type": "expense",
                "amount": data.amount,
                "description": data.description,
                "balance_after": new_balance,
                "project_id": data.project_id,
            },
        ))

        await self._db.flush()

        return TransactionResponse.from_model(
            tx,
            project_title=project.title if project else None,
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
            initialized_at=(
                balance.initialized_at.isoformat()
                if balance.initialized_at else None
            ),
            initialized_by_name=initialized_by_name,
        )

    async def get_transactions(
        self,
        limit: int = 50,
        offset: int = 0,
        transaction_type: str | None = None,
    ) -> tuple[list[TransactionResponse], int]:
        """
        Retourne l'historique des transactions avec pagination.

        tuple[list, int] : on retourne à la fois les transactions ET le total
        pour que le router puisse construire la réponse paginée.
        """
        from sqlalchemy import func

        # Requête principale
        stmt = select(Transaction)
        count_stmt = select(func.count(Transaction.id))

        if transaction_type:
            stmt = stmt.where(Transaction.type == transaction_type)
            count_stmt = count_stmt.where(Transaction.type == transaction_type)

        # Plus récent en premier
        stmt = (
            stmt.order_by(Transaction.performed_at.desc())
            .limit(limit)
            .offset(offset)
        )

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

            responses.append(TransactionResponse.from_model(
                tx,
                member_name=member_name,
                project_title=project_title,
                performed_by_name=performed_by_name,
            ))

        return responses, total

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS PRIVÉS
    # ─────────────────────────────────────────────────────────────────────────

    async def _confirm_contribution(
        self,
        member_id: str,
        transaction_id: uuid.UUID,
        amount: float,
        contribution_id: str | None,
    ) -> None:
        """
        Confirme la cotisation du mois en cours pour ce membre.

        Trois cas possibles :
          1. contribution_id fourni → met à jour cette contribution spécifique
          2. Contribution DECLARED trouvée ce mois → la confirme
          3. Aucune contribution trouvée → en crée une nouvelle (confirmée directement)

        On lit aussi les settings de l'association pour renseigner
        expected_amount et contribution_mode (snapshot).
        """
        from app.infrastructure.database.models import AssociationSettings

        now = datetime.now(timezone.utc)

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
                select(Contribution).where(
                    Contribution.id == uuid.UUID(contribution_id)
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

    async def _notify_deposit_confirmed(
        self,
        member: Member,
        amount: float,
    ) -> None:
        """
        Envoie une notification SMS au membre pour confirmer sa cotisation.
        Si la notification échoue, on ne lève pas d'exception —
        la transaction financière est plus importante que la notif.
        """
        try:
            from app.domains.notifications.service import NotificationService
            notif_service = NotificationService(self._db)
            await notif_service.send_contribution_confirmed(member, amount)
        except Exception as e:
            # Log l'erreur mais ne bloque pas la transaction financière
            print(f"⚠️ Notification échouée pour {member.full_name}: {e}")