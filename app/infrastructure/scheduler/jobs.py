"""
scheduler/jobs.py — Tâches automatiques planifiées.

APScheduler tourne en arrière-plan dans la même boucle async que FastAPI.
Il démarre avec l'application (dans le lifespan de main.py)
et s'arrête proprement quand l'application s'arrête.

Tâche actuelle :
  send_contribution_reminders : chaque jour entre le 1er et le 15 du mois,
  à l'heure configurée, envoie un rappel SMS aux membres qui n'ont pas
  encore cotisé ce mois.

Pourquoi du code async dans le job ?
  Les jobs APScheduler avec AsyncIOScheduler peuvent être des coroutines
  (async def). Le scheduler les lance dans la boucle d'événements FastAPI.
  Ça permet d'utiliser SQLAlchemy async et nos services normalement.

Nouvelle session dans le job :
  Le job ne reçoit pas de session DB via Depends() comme les routes.
  Il crée sa propre session via AsyncSessionLocal() directement.
  C'est le seul endroit dans le projet où on fait ça — partout ailleurs
  on utilise Depends(get_db).
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

# Instance globale du scheduler — créée une fois au démarrage
_scheduler: AsyncIOScheduler | None = None


async def send_contribution_reminders() -> None:
    """
    Envoie des rappels SMS aux membres qui n'ont pas encore cotisé.

    Exécuté chaque jour entre le 1er et le 15 du mois.
    Ne fait rien hors de cette période.

    Logique :
      1. Vérifie qu'on est dans la période de cotisation
      2. Charge les membres sans cotisation confirmée
      3. Charge les settings (montant fixe si applicable)
      4. Envoie un rappel à chacun
    """
    from datetime import datetime, timezone

    from app.domains.contributions.service import ContributionService
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

    print(f"⏰ Lancement des rappels cotisation — {now.strftime('%d/%m/%Y %H:%M')}")

    # Crée une nouvelle session DB pour ce job
    # (les jobs n'ont pas accès au système Depends de FastAPI)
    async with AsyncSessionLocal() as db:
        try:
            # Charge le mode de cotisation
            settings_result = await db.execute(select(AssociationSettings))
            asso_settings = settings_result.scalar_one_or_none()

            amount = None
            if asso_settings and asso_settings.contribution_mode == "fixed":
                if asso_settings.fixed_amount:
                    amount = float(asso_settings.fixed_amount)

            # Charge les membres sans cotisation confirmée
            contribution_service = ContributionService(db)
            members_to_remind = await contribution_service.get_members_without_contribution(
                month=now.month,
                year=now.year,
            )

            if not members_to_remind:
                print("✅ Tous les membres ont cotisé ce mois — aucun rappel nécessaire")
                await db.commit()
                return

            # Envoie les rappels
            notification_service = NotificationService(db)
            sent_count = 0

            for member in members_to_remind:
                try:
                    await notification_service.send_contribution_reminder(
                        member=member,
                        month=now.month,
                        year=now.year,
                        amount=amount,
                    )
                    sent_count += 1
                except Exception as e:
                    # Si le rappel d'un membre échoue, on continue avec le suivant
                    print(f"⚠️ Rappel échoué pour {member.full_name} : {e}")

            await db.commit()
            print(
                f"✅ Rappels cotisation envoyés : {sent_count}/{len(members_to_remind)} membres"
            )

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

    # Tâche : rappels cotisation
    # CronTrigger(hour=9, minute=0) = chaque jour à 09h00
    _scheduler.add_job(
        func=send_contribution_reminders,
        trigger=CronTrigger(
            hour=settings.contribution_reminder_hour,
            minute=0,
        ),
        id="contribution_reminder",
        name="Rappels cotisation mensuelle",
        replace_existing=True,
        # misfire_grace_time : si le job n'a pas pu tourner à l'heure prévue
        # (ex: serveur redémarrait), il a 1h pour se rattraper
        misfire_grace_time=3600,
    )

    _scheduler.start()
    print(
        f"⏰ Scheduler démarré — "
        f"rappels cotisation à {settings.contribution_reminder_hour}h00 "
        f"(jours {settings.contribution_reminder_start_day}"
        f"–{settings.contribution_reminder_end_day} de chaque mois)"
    )


def stop_scheduler() -> None:
    """
    Arrête proprement le scheduler.
    Appelé dans le lifespan de main.py à l'arrêt de l'application.
    wait=False : ne pas attendre la fin des jobs en cours
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        print("⏰ Scheduler arrêté")