"""
notifications/router.py — Endpoints HTTP pour les notifications broadcast.

Toutes les notifications sont communes (broadcast).
La lecture est individuelle : is_read est calculé par membre.

Routes :
  GET    /notifications                  → liste des broadcasts (avec is_read)
  GET    /notifications?unread_only=true → seulement les non lues
  GET    /notifications/count            → nombre de non-lues (badge 🔔)
  PATCH  /notifications/{id}/read        → marquer une notif comme lue
  PATCH  /notifications/read-all         → marquer toutes comme lues

Note : les routes PATCH modifient uniquement broadcast_reads pour
le membre connecté — les autres membres ne sont pas affectés.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_member
from app.domains.notifications.service import NotificationService
from app.infrastructure.database.session import get_db
from app.shared.response import success_response

router = APIRouter()


@router.get("")
async def get_notifications(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    unread_only: bool = Query(default=False),
):
    """
    Retourne les notifications broadcast du membre connecté.

    Chaque notification inclut is_read calculé pour CE membre :
      - is_read: true  → ce membre a déjà lu cette notification
      - is_read: false → ce membre ne l'a pas encore lue

    Triées par date décroissante (plus récent en premier).
    Limitées aux 50 dernières.

    ?unread_only=true → seulement les non lues pour ce membre
    """
    service = NotificationService(db)
    notifications = await service.get_broadcasts(
        member_id=str(current_member.id),
        unread_only=unread_only,
    )

    return success_response(
        data=notifications,
        message=f"{len(notifications)} notification(s)",
    )


@router.get("/count")
async def get_unread_count(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne le nombre de notifications non lues pour CE membre.

    Utilisé par le frontend pour afficher le badge (ex: 🔔 3).
    Route légère — ne charge que le count, pas les données complètes.
    """
    service = NotificationService(db)
    count = await service.get_unread_count(str(current_member.id))

    return success_response(data={"unread_count": count})


@router.patch("/read-all")
async def mark_all_as_read(
    current_member=Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Marque toutes les notifications non lues comme lues pour CE membre.

    N'affecte PAS les autres membres — chacun a son propre suivi de lecture.
    Appelé quand le membre ouvre le panneau de notifications.
    """
    service = NotificationService(db)
    count = await service.mark_all_broadcasts_read(str(current_member.id))
    await db.commit()

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
    Marque une notification spécifique comme lue pour CE membre.

    Les autres membres conservent leur statut de lecture indépendant.
    Si déjà lue, retourne succès sans erreur (idempotent).
    """
    service = NotificationService(db)
    found = await service.mark_broadcast_as_read(
        notification_id=notification_id,
        member_id=str(current_member.id),
    )
    await db.commit()

    if not found:
        return success_response(message="Notification introuvable")

    return success_response(message="Notification marquée comme lue")