"""
channels/inapp.py — Notifications stockées en base de données.

Ce canal ne "envoie" rien physiquement — il insère une ligne
dans la table 'notifications' de PostgreSQL.

Le frontend récupère ces notifications via GET /notifications
et les affiche dans une cloche (🔔) ou un badge.

Différence avec SMS :
  SMS   : push — on pousse l'info vers le membre (il reçoit sans rien demander)
  InApp : pull — le membre ouvre l'app et voit les notifications

Les deux sont complémentaires :
  SMS   = alerte immédiate sur le téléphone
  InApp = historique consultable à tout moment
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.notifications.channels.base import (
    BaseNotificationChannel,
    NotificationPayload,
)
from app.shared.enums import NotificationChannel, NotificationStatus, NotificationType


class InAppChannel(BaseNotificationChannel):
    """
    Canal in-app : insère la notification en base de données.

    Contrairement à SMSChannel qui est initialisé une fois,
    InAppChannel reçoit la session DB à chaque instanciation
    car il doit écrire en base.
    """

    def __init__(self, db: AsyncSession) -> None:
        """
        Reçoit la session DB par injection.
        La même session que celle utilisée par le service appelant
        → tout s'inscrit dans la même transaction.
        """
        self._db = db

    def channel_name(self) -> str:
        return "in_app"

    async def send(self, payload: NotificationPayload) -> bool:
        """
        Insère la notification en BDD.

        On utilise flush() et non pas commit() — le commit
        est géré par session.py à la fin de la requête HTTP.
        Ainsi, si quelque chose échoue plus tard dans la même
        requête, la notification est aussi annulée (cohérence).
        """
        # Import local pour éviter les imports circulaires
        # (models.py est importé par beaucoup de modules)
        from app.infrastructure.database.models import Notification

        try:
            notification = Notification(
                member_id=uuid.UUID(payload.recipient_id),
                channel=NotificationChannel.IN_APP,
                # NotificationType(value) : convertit la string en enum
                # Ex : "contribution_reminder" → NotificationType.CONTRIBUTION_REMINDER
                type=NotificationType(payload.notification_type),
                status=NotificationStatus.SENT,
                content=payload.message,
                sent_at=datetime.now(timezone.utc),
            )
            self._db.add(notification)
            # flush() envoie l'INSERT à PostgreSQL sans committer
            await self._db.flush()
            return True

        except Exception as e:
            print(f"❌ Erreur InApp pour {payload.recipient_id} : {e}")
            return False