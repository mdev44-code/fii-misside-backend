"""
scheduler/jobs.py — Tâches automatiques planifiées.

Changement par rapport à la version précédente :
  Avant : boucle sur chaque membre → N notifications individuelles
  Maintenant : 1 seul broadcast → 1 ligne dans broadcast_notifications

Le scheduler ne s'occupe plus de savoir qui a cotisé ou pas.
Il envoie simplement un rappel commun à tout le monde
entre le 1er et le 15 de chaque mois.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

_scheduler: AsyncIOScheduler | None = None


async def send_contribution_reminder_broadcast() -> None:
    """
    Envoie un rappel de cotisation broadcast (1 seule ligne en BDD).

    Exécuté chaque jour entre le 1er et le 15 du mois.
    Ne fait rien hors de cette période.

    Logique simplifiée par rapport à l'ancienne version :
      - Plus de boucle sur les membres
      - Plus de vérification de qui a cotisé
      - 1 seul INSERT dans broadcast_notifications
      - Tous les membres verront le rappel à leur prochaine connexion
    """
    from datetime import datetime, timezone

    from app.domains.notifications.service import NotificationService
    from app.infrastructure.database.models import AssociationSettings
    from app.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import select

    now = datetime.now(timezone.utc)

    # Vérifie qu'on est dans la période de rappel (1er au 15)
    if not (
        settings.contribution_reminder_start_day
        <= now.day
        <= settings.contribution_reminder_end_day
    ):
        print(
            f"⏰ Rappel cotisation ignoré — "
            f"hors période (jour {now.day}, "
            f"période : {settings.contribution_reminder_start_day}"
            f"–{settings.contribution_reminder_end_day})"
        )
        return

    print(f"⏰ Lancement du broadcast rappel cotisation — {now.strftime('%d/%m/%Y %H:%M')}")

    async with AsyncSessionLocal() as db:
        try:
            # Charge le montant fixe si applicable
            settings_result = await db.execute(select(AssociationSettings))
            asso_settings = settings_result.scalar_one_or_none()

            amount = None
            if asso_settings and asso_settings.contribution_mode == "fixed":
                if asso_settings.fixed_amount:
                    amount = float(asso_settings.fixed_amount)

            # 1 seul broadcast pour tout le monde
            notification_service = NotificationService(db)
            await notification_service.send_contribution_reminder_broadcast(
                month=now.month,
                year=now.year,
                amount=amount,
            )

            await db.commit()
            print("✅ Broadcast rappel cotisation créé avec succès")

        except Exception as e:
            await db.rollback()
            print(f"❌ Erreur dans le job de rappel : {e}")


def start_scheduler() -> None:
    """
    Démarre le scheduler au démarrage de l'application.
    Appelé dans le lifespan de main.py.

    timezone="Africa/Dakar" : les horaires sont en heure locale Dakar
    (UTC+0 toute l'année, pas de changement d'heure)
    """
    global _scheduler

    _scheduler = AsyncIOScheduler(timezone="Africa/Dakar")

    _scheduler.add_job(
        func=send_contribution_reminder_broadcast,
        trigger=CronTrigger(
            hour=settings.contribution_reminder_hour,
            minute=0,
        ),
        id="contribution_reminder_broadcast",
        name="Rappel cotisation mensuelle (broadcast)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    print(
        f"⏰ Scheduler démarré — "
        f"rappel cotisation broadcast à {settings.contribution_reminder_hour}h00 "
        f"(jours {settings.contribution_reminder_start_day}"
        f"–{settings.contribution_reminder_end_day} de chaque mois)"
    )


def stop_scheduler() -> None:
    """
    Arrête proprement le scheduler.
    Appelé dans le lifespan de main.py à l'arrêt de l'application.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("⏰ Scheduler arrêté")