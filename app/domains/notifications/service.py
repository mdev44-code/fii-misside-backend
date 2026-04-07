"""
notifications/service.py — Orchestrateur des canaux de notification.

Ce service centralise :
  1. La construction des messages (templates)
  2. Le choix des canaux à utiliser selon le contexte
  3. L'enregistrement du statut d'envoi en BDD
  4. La lecture des notifications in-app d'un membre

Pattern utilisé — Strategy :
  _dispatch() reçoit une liste de canaux et les appelle tous.
  Le service ne sait pas comment SMS ou InApp fonctionnent,
  juste qu'ils respectent l'interface BaseNotificationChannel.

Les templates de messages sont des méthodes privées (_contribution_reminder_text...)
pour les garder centralisés et faciles à modifier.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.notifications.channels.base import (
    BaseNotificationChannel,
    NotificationPayload,
)
from app.domains.notifications.channels.inapp import InAppChannel
from app.domains.notifications.channels.sms import SMSChannel
from app.infrastructure.database.models import Member, Notification
from app.shared.enums import (
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)


class NotificationService:

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        # On instancie SMSChannel une seule fois par service
        # InAppChannel reçoit la session à chaque appel (il écrit en BDD)
        self._sms_channel = SMSChannel()

    # ─────────────────────────────────────────────────────────────────────────
    # TEMPLATES DE MESSAGES
    # ─────────────────────────────────────────────────────────────────────────

    def _contribution_reminder_text(
        self,
        member_name: str,
        month: int,
        year: int,
        amount: float | None,
    ) -> str:
        """
        Message de rappel de cotisation.
        Adapté selon le mode (libre ou fixe).
        """
        # Noms des mois en français
        month_names = [
            "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
            "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
        ]
        month_name = month_names[month]

        if amount:
            return (
                f"Bonjour {member_name}, rappel : cotisez {amount:,.0f} FCFA "
                f"pour {month_name} {year} via Wave au "
                f"{settings.wave_treasurer_number}. Merci !"
            )
        else:
            return (
                f"Bonjour {member_name}, rappel : pensez à cotiser "
                f"pour {month_name} {year} via Wave au "
                f"{settings.wave_treasurer_number}. Merci !"
            )

    def _contribution_confirmed_text(
        self,
        member_name: str,
        amount: float,
    ) -> str:
        """Message de confirmation de réception de cotisation."""
        return (
            f"Bonjour {member_name}, votre cotisation de "
            f"{amount:,.0f} FCFA a bien été reçue et confirmée. Merci !"
        )

    def _new_project_text(self, project_title: str) -> str:
        """Message d'annonce d'un nouveau projet."""
        return (
            f"Nouveau projet créé : '{project_title}'. "
            f"Consultez l'application pour les détails."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MÉTHODES PUBLIQUES — appelées par les services métier
    # ─────────────────────────────────────────────────────────────────────────

    async def send_contribution_reminder(
        self,
        member: Member,
        month: int,
        year: int,
        amount: float | None = None,
    ) -> None:
        """
        Rappel de cotisation mensuelle.
        Envoyé via SMS + InApp.
        Appelé par le cron job du scheduler.
        """
        message = self._contribution_reminder_text(
            member.full_name, month, year, amount
        )
        await self._dispatch(
            member=member,
            message=message,
            notification_type=NotificationType.CONTRIBUTION_REMINDER,
            channels=[NotificationChannel.SMS, NotificationChannel.IN_APP],
        )

    async def send_contribution_confirmed(
        self,
        member: Member,
        amount: float,
    ) -> None:
        """
        Confirmation de réception de cotisation.
        Envoyé via SMS + InApp.
        Appelé par treasury/service.py après confirm_deposit().
        """
        message = self._contribution_confirmed_text(member.full_name, amount)
        await self._dispatch(
            member=member,
            message=message,
            notification_type=NotificationType.CONTRIBUTION_CONFIRMED,
            channels=[NotificationChannel.SMS, NotificationChannel.IN_APP],
        )

    async def send_new_project_notification(
        self,
        members: list[Member],
        project_title: str,
    ) -> None:
        """
        Annonce d'un nouveau projet à tous les membres.
        Envoyé uniquement via InApp (pas de SMS pour ça).
        """
        message = self._new_project_text(project_title)
        for member in members:
            await self._dispatch(
                member=member,
                message=message,
                notification_type=NotificationType.NEW_PROJECT,
                channels=[NotificationChannel.IN_APP],
            )

    # ─────────────────────────────────────────────────────────────────────────
    # LECTURE DES NOTIFICATIONS IN-APP
    # ─────────────────────────────────────────────────────────────────────────

    async def get_member_notifications(
        self,
        member_id: str,
        unread_only: bool = False,
    ) -> list[Notification]:
        """
        Retourne les notifications in-app d'un membre.
        Triées par date décroissante (plus récent en premier).
        Limitées aux 50 dernières pour la performance.
        """
        import uuid as uuid_module

        stmt = (
            select(Notification)
            .where(
                Notification.member_id == uuid_module.UUID(member_id),
                Notification.channel == NotificationChannel.IN_APP,
            )
        )

        if unread_only:
            # read_at IS NULL = notification non lue
            stmt = stmt.where(Notification.read_at.is_(None))

        stmt = stmt.order_by(Notification.created_at.desc()).limit(50)

        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def mark_as_read(
        self,
        notification_id: str,
        member_id: str,
    ) -> bool:
        """
        Marque une notification comme lue.

        On vérifie que la notification appartient bien au membre
        (sécurité — un membre ne peut pas marquer la notif d'un autre).

        Retourne True si trouvée et marquée, False sinon.
        """
        import uuid as uuid_module

        result = await self._db.execute(
            select(Notification).where(
                Notification.id == uuid_module.UUID(notification_id),
                Notification.member_id == uuid_module.UUID(member_id),
            )
        )
        notification = result.scalar_one_or_none()

        if not notification:
            return False

        if not notification.read_at:
            notification.read_at = datetime.now(timezone.utc)
            notification.status = NotificationStatus.READ

        return True

    async def mark_all_as_read(self, member_id: str) -> int:
        """
        Marque toutes les notifications non lues d'un membre comme lues.
        Retourne le nombre de notifications marquées.
        """
        import uuid as uuid_module

        result = await self._db.execute(
            select(Notification).where(
                Notification.member_id == uuid_module.UUID(member_id),
                Notification.channel == NotificationChannel.IN_APP,
                Notification.read_at.is_(None),
            )
        )
        notifications = result.scalars().all()

        now = datetime.now(timezone.utc)
        count = 0
        for notif in notifications:
            notif.read_at = now
            notif.status = NotificationStatus.READ
            count += 1

        return count

    # ─────────────────────────────────────────────────────────────────────────
    # DISPATCHER INTERNE
    # ─────────────────────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        member: Member,
        message: str,
        notification_type: NotificationType,
        channels: list[NotificationChannel],
    ) -> None:
        """
        Envoie la notification sur chaque canal demandé.

        Pour chaque canal :
          1. Instancie le canal approprié
          2. Appelle send()
          3. Si SMS : enregistre le statut en BDD (succès ou échec)
             Si InApp : le canal s'enregistre lui-même dans send()

        On n'interrompt pas si un canal échoue — on continue
        avec les autres canaux.
        """
        payload = NotificationPayload(
            recipient_phone=member.phone_number,
            recipient_id=str(member.id),
            message=message,
            notification_type=notification_type.value,
        )

        for channel in channels:
            if channel == NotificationChannel.SMS:
                await self._send_via_sms(payload, member, notification_type)
            elif channel == NotificationChannel.IN_APP:
                await self._send_via_inapp(payload)

    async def _send_via_sms(
        self,
        payload: NotificationPayload,
        member: Member,
        notification_type: NotificationType,
    ) -> None:
        """
        Envoie via SMS et enregistre le résultat en BDD.

        On enregistre le SMS dans la table notifications même en cas d'échec
        → permet de savoir combien de SMS ont échoué et de les réessayer.
        """
        success = await self._sms_channel.send(payload)

        # Enregistre le résultat dans la table notifications
        from app.infrastructure.database.models import Notification

        sms_record = Notification(
            member_id=member.id,
            channel=NotificationChannel.SMS,
            type=notification_type,
            status=NotificationStatus.SENT if success else NotificationStatus.FAILED,
            content=payload.message,
            sent_at=datetime.now(timezone.utc),
        )
        self._db.add(sms_record)

    async def _send_via_inapp(self, payload: NotificationPayload) -> None:
        """
        Envoie via le canal in-app.
        InAppChannel s'occupe lui-même de l'enregistrement en BDD.
        """
        inapp = InAppChannel(self._db)
        await inapp.send(payload)