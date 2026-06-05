import uuid as uuid_module
from datetime import datetime, timezone

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Member
from app.shared.enums import NotificationType


class NotificationService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # TEMPLATES DE MESSAGES
    # ─────────────────────────────────────────────────────────────────────────

    def _contribution_reminder_text(self, month: int, year: int, amount: float | None) -> str:
        """Rappel mensuel de cotisation — déclenché automatiquement."""
        month_names = [
            "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
            "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
        ]
        month_name = month_names[month]
        if amount:
            return (
                f"Rappel : la période de cotisation pour {month_name} {year} "
                f"est ouverte. Montant : {amount:,.0f} FCFA. Pensez à cotiser avant le 15 !"
            )
        return (
            f"Rappel : la période de cotisation pour {month_name} {year} "
            f"est ouverte. Pensez à cotiser avant le 15 !"
        )

    def _contribution_received_text(self, member_name: str, amount: float) -> str:
        """Annonce qu'un membre a cotisé — déclenché par le comptable."""
        return (
            f"{member_name} a cotisé {amount:,.0f} FCFA. "
            f"Merci pour sa contribution !"
        )

    def _expense_text(self, description: str, amount: float) -> str:
        """Annonce d'une dépense enregistrée par le comptable."""
        return (
            f"Nouvelle dépense : {description} — "
            f"{amount:,.0f} FCFA."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTHODES PUBLIQUES — appelées par les services métier
    # ─────────────────────────────────────────────────────────────────────────

    async def send_contribution_reminder_broadcast(
        self,
        month: int,
        year: int,
        amount: float | None = None,
    ) -> None:
        """
        Rappel de cotisation mensuelle — broadcast commun.

        Appelé UNE SEULE FOIS par le scheduler (pas de boucle par membre).
        Crée un seul enregistrement dans broadcast_notifications.

        triggered_by = NULL car c'est automatique (pas d'action humaine).
        """
        message = self._contribution_reminder_text(month, year, amount)
        await self._create_broadcast(
            content=message,
            notification_type=NotificationType.CONTRIBUTION_REMINDER,
            triggered_by=None,
        )

    async def send_contribution_received_broadcast(
        self,
        member_name: str,
        amount: float,
        triggered_by_id: str,
    ) -> None:
        """
        Annonce de cotisation reçue — broadcast commun.

        Appelé par treasury/service.py après confirm_deposit().

        triggered_by_id : UUID du comptable qui a confirmé.
        member_name     : nom du membre qui a cotisé (pour le message).
        """
        message = self._contribution_received_text(member_name, amount)
        await self._create_broadcast(
            content=message,
            notification_type=NotificationType.CONTRIBUTION_RECEIVED,
            triggered_by=triggered_by_id,
        )

    async def send_expense_broadcast(
        self,
        description: str,
        amount: float,
        triggered_by_id: str,
    ) -> None:
        """
        Annonce d'une dépense enregistrée — broadcast commun.

        Appelé par treasury/service.py après record_expense().

        triggered_by_id : UUID du comptable qui a enregistré la dépense.
        """
        message = self._expense_text(description, amount)
        await self._create_broadcast(
            content=message,
            notification_type=NotificationType.EXPENSE_RECORDED,
            triggered_by=triggered_by_id,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE — appelées par le router
    # ─────────────────────────────────────────────────────────────────────────

    async def get_broadcasts(
        self,
        member_id: str,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """
        Retourne les broadcast_notifications avec is_read calculé
        pour le membre connecté.

        Logique :
          is_read = True si une ligne existe dans broadcast_reads
                    pour (notification_id, member_id).

        On utilise une LEFT JOIN pour récupérer toutes les notifs
        et savoir pour chacune si ce membre l'a lue.

        Retourne une liste de dicts prêts à sérialiser en JSON.
        """
        from app.infrastructure.database.models import (
            BroadcastNotification,
            BroadcastRead,
        )

        member_uuid = uuid_module.UUID(member_id)

        # LEFT JOIN : toutes les notifs + lecture du membre si elle existe
        stmt = (
            select(
                BroadcastNotification,
                BroadcastRead.read_at,
            )
            .outerjoin(
                BroadcastRead,
                and_(
                    BroadcastRead.notification_id == BroadcastNotification.id,
                    BroadcastRead.member_id == member_uuid,
                ),
            )
            .order_by(BroadcastNotification.created_at.desc())
            .limit(limit)
        )

        if unread_only:
            # IS NULL = pas de ligne dans broadcast_reads = non lue
            stmt = stmt.where(BroadcastRead.read_at.is_(None))

        result = await self._db.execute(stmt)
        rows = result.all()

        return [
            {
                "id": str(notif.id),
                "type": notif.type,
                "content": notif.content,
                "triggered_by": str(notif.triggered_by) if notif.triggered_by else None,
                "is_read": read_at is not None,
                "read_at": read_at.isoformat() if read_at else None,
                "created_at": notif.created_at.isoformat(),
            }
            for notif, read_at in rows
        ]

    async def mark_broadcast_as_read(
        self,
        notification_id: str,
        member_id: str,
    ) -> bool:
        """
        Marque une notification broadcast comme lue pour CE membre uniquement.

        Insère une ligne dans broadcast_reads(notification_id, member_id).
        Si déjà lue (ligne existante), ne fait rien (idempotent).

        Retourne True si trouvée, False si la notification n'existe pas.
        """
        from app.infrastructure.database.models import (
            BroadcastNotification,
            BroadcastRead,
        )

        notif_uuid = uuid_module.UUID(notification_id)
        member_uuid = uuid_module.UUID(member_id)

        # Vérifie que la notification existe
        notif_result = await self._db.execute(
            select(BroadcastNotification).where(
                BroadcastNotification.id == notif_uuid
            )
        )
        if not notif_result.scalar_one_or_none():
            return False

        # Vérifie si déjà lue pour éviter le doublon (PK composite protège aussi)
        existing = await self._db.execute(
            select(BroadcastRead).where(
                BroadcastRead.notification_id == notif_uuid,
                BroadcastRead.member_id == member_uuid,
            )
        )
        if existing.scalar_one_or_none():
            return True  # Déjà lue — idempotent

        # Enregistre la lecture
        read = BroadcastRead(
            notification_id=notif_uuid,
            member_id=member_uuid,
            read_at=datetime.now(timezone.utc),
        )
        self._db.add(read)
        await self._db.flush()

        return True

    async def mark_all_broadcasts_read(self, member_id: str) -> int:
        """
        Marque toutes les notifications non lues comme lues pour ce membre.

        Utile quand le membre ouvre le panneau de notifications.
        Retourne le nombre de nouvelles lectures enregistrées.
        """
        from app.infrastructure.database.models import (
            BroadcastNotification,
            BroadcastRead,
        )

        member_uuid = uuid_module.UUID(member_id)

        # Récupère les notifs non lues par ce membre
        stmt = (
            select(BroadcastNotification.id)
            .outerjoin(
                BroadcastRead,
                and_(
                    BroadcastRead.notification_id == BroadcastNotification.id,
                    BroadcastRead.member_id == member_uuid,
                ),
            )
            .where(BroadcastRead.read_at.is_(None))
        )
        result = await self._db.execute(stmt)
        unread_ids = [row[0] for row in result.all()]

        if not unread_ids:
            return 0

        now = datetime.now(timezone.utc)
        for notif_id in unread_ids:
            self._db.add(
                BroadcastRead(
                    notification_id=notif_id,
                    member_id=member_uuid,
                    read_at=now,
                )
            )

        await self._db.flush()
        return len(unread_ids)

    async def get_unread_count(self, member_id: str) -> int:
        """
        Retourne le nombre de notifications non lues pour ce membre.

        Utilisé par le frontend pour afficher le badge 🔔.
        Route légère — ne charge que le COUNT.

        Non lue = aucune ligne dans broadcast_reads pour ce membre.
        """
        from app.infrastructure.database.models import (
            BroadcastNotification,
            BroadcastRead,
        )

        member_uuid = uuid_module.UUID(member_id)

        stmt = (
            select(func.count(BroadcastNotification.id))
            .outerjoin(
                BroadcastRead,
                and_(
                    BroadcastRead.notification_id == BroadcastNotification.id,
                    BroadcastRead.member_id == member_uuid,
                ),
            )
            .where(BroadcastRead.read_at.is_(None))
        )

        result = await self._db.execute(stmt)
        return result.scalar() or 0

    # ─────────────────────────────────────────────────────────────────────────
    # DISPATCHER INTERNE
    # ─────────────────────────────────────────────────────────────────────────

    async def _create_broadcast(
        self,
        content: str,
        notification_type: NotificationType,
        triggered_by: str | None,
    ) -> None:
        """
        Insère une ligne dans broadcast_notifications.

        C'est tout ce qu'on fait : pas de boucle, pas de canal externe.
        Chaque membre verra cette notif à sa prochaine requête GET /notifications/broadcast.
        """
        from app.infrastructure.database.models import BroadcastNotification

        notif = BroadcastNotification(
            type=notification_type.value,
            content=content,
            triggered_by=(
                uuid_module.UUID(triggered_by) if triggered_by else None
            ),
        )
        self._db.add(notif)
        await self._db.flush()

        print(
            f"📢 Broadcast créé : [{notification_type.value}] "
            f"{content[:60]}{'...' if len(content) > 60 else ''}"
        )