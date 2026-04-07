"""
notifications/router.py — Endpoints HTTP pour les notifications in-app.

Routes :
  GET    /notifications          → liste des notifs du membre connecté
  GET    /notifications?unread=true → seulement les non lues
  PATCH  /notifications/{id}/read   → marquer une notif comme lue
  PATCH  /notifications/read-all    → marquer toutes comme lues
  GET    /notifications/count       → nombre de notifs non lues (pour le badge)

Ces routes concernent uniquement les notifications IN-APP.
Les SMS n'ont pas de route de lecture — ils partent et c'est tout.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.notifications.service import NotificationService
from app.infrastructure.database.session import get_db
from app.shared.response import success_response

router = APIRouter()


@router.get("")
async def get_my_notifications(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    unread_only: bool = Query(default=False),
):
    """
    Retourne les notifications in-app du membre connecté.
    Triées par date décroissante, limitées aux 50 dernières.

    ?unread_only=true → seulement les non lues
    """
    service = NotificationService(db)
    notifications = await service.get_member_notifications(
        member_id=str(current_member.id),
        unread_only=unread_only,
    )

    return success_response(
        data=[
            {
                "id": str(n.id),
                "type": n.type,
                "content": n.content,
                "status": n.status,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "is_read": n.read_at is not None,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ],
        message=f"{len(notifications)} notification(s)",
    )


@router.get("/count")
async def get_unread_count(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne le nombre de notifications non lues.
    Utilisé par le frontend pour afficher le badge (ex: 🔔 3).
    Route légère — ne charge que le count, pas les données complètes.
    """
    service = NotificationService(db)
    notifications = await service.get_member_notifications(
        member_id=str(current_member.id),
        unread_only=True,
    )

    return success_response(
        data={"unread_count": len(notifications)}
    )


@router.patch("/read-all")
async def mark_all_as_read(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Marque toutes les notifications non lues du membre comme lues.
    Appelé quand le membre ouvre le panneau de notifications.
    """
    service = NotificationService(db)
    count = await service.mark_all_as_read(str(current_member.id))

    return success_response(
        message=f"{count} notification(s) marquée(s) comme lue(s)"
    )


@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Marque une notification spécifique comme lue.

    On vérifie que la notification appartient bien au membre connecté
    — un membre ne peut pas marquer la notif d'un autre.
    """
    service = NotificationService(db)
    found = await service.mark_as_read(
        notification_id=notification_id,
        member_id=str(current_member.id),
    )

    if not found:
        return success_response(message="Notification introuvable")

    return success_response(message="Notification marquée comme lue")