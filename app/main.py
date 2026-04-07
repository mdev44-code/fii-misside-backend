"""
app/main.py — Point d'entrée de l'application FastAPI.

Ce fichier fait trois choses :
  1. Définit le lifespan (démarrage et arrêt propre)
  2. Crée l'application FastAPI avec tous ses middlewares
  3. Inclut tous les routers avec leurs préfixes

Ordre d'exécution au démarrage :
  1. lifespan startup : Redis → Scheduler
  2. FastAPI est prêt à recevoir des requêtes
  3. lifespan shutdown (à l'arrêt) : Scheduler → Redis

Lancement :
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  ou via : make dev
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.shared.exceptions import AppException
from app.shared.response import error_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gère le cycle de vie complet de l'application.

    @asynccontextmanager transforme cette fonction en gestionnaire de contexte.
    La syntaxe 'async with' de FastAPI appelle :
      - Tout avant yield  → au démarrage
      - Tout après yield  → à l'arrêt (même en cas d'erreur)

    yield sans valeur : on ne passe rien à FastAPI, juste on suspend
    l'exécution le temps que l'app tourne.
    """
    # ── STARTUP ───────────────────────────────────────────────────────────────
    print(f"\n🚀 Démarrage de {settings.app_name} v{settings.app_version}")
    print(f"   Environnement : {settings.environment}")

    # 1. Connexion Redis (tokens, cache)
    from app.infrastructure.cache.redis import init_redis
    await init_redis()

    # 2. Démarrage du scheduler (cron jobs)
    from app.infrastructure.scheduler.jobs import start_scheduler
    start_scheduler()

    print(f"✅ Application prête sur http://0.0.0.0:8000\n")

    yield  # ← L'application tourne ici

    # ── SHUTDOWN ──────────────────────────────────────────────────────────────
    print("\n🛑 Arrêt de l'application...")

    from app.infrastructure.scheduler.jobs import stop_scheduler
    stop_scheduler()

    from app.infrastructure.cache.redis import close_redis
    await close_redis()

    print("✅ Arrêt propre terminé\n")


def create_app() -> FastAPI:
    """
    Factory function : crée et configure l'application FastAPI.

    On utilise une factory plutôt que de définir 'app' directement
    au niveau du module pour faciliter les tests (on peut créer
    plusieurs instances avec des configs différentes).
    """
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="API de gestion d'association villageoise",
        # En production, on cache la doc Swagger pour la sécurité
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── MIDDLEWARES ───────────────────────────────────────────────────────────

    # CORS : autorise le frontend React à appeler l'API depuis un autre domaine
    # Sans ça, le navigateur bloque toutes les requêtes cross-origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,  # ["http://localhost:5173", ...]
        allow_credentials=True,               # autorise les cookies et headers d'auth
        allow_methods=["*"],                  # GET, POST, PATCH, DELETE...
        allow_headers=["*"],                  # Authorization, Content-Type...
    )

    # ── GESTIONNAIRES D'EXCEPTIONS GLOBAUX ────────────────────────────────────

    @app.exception_handler(AppException)
    async def app_exception_handler(
        request: Request,
        exc: AppException,
    ) -> JSONResponse:
        """
        Intercepte toutes nos exceptions métier (NotFoundError, ForbiddenError...)
        et les convertit en réponse JSON uniforme.

        Sans ce handler, FastAPI retournerait une erreur 500 générique
        au lieu de notre message d'erreur structuré.

        Exemple :
          raise NotFoundError("Membre")
          → {"success": false, "message": "Membre introuvable", "data": {...}}
        """
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                message=exc.message,
                data={
                    "error_code": exc.error_code,
                    "details": exc.details,
                },
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Dernier recours : intercepte toutes les exceptions non prévues.

        En développement (debug=True) : on re-lève l'exception pour voir
        la traceback complète dans le terminal.

        En production : on retourne un message générique pour ne pas
        exposer les détails internes au client.
        """
        if settings.debug:
            raise exc  # traceback visible dans le terminal en dev

        return JSONResponse(
            status_code=500,
            content=error_response(
                "Une erreur interne est survenue. Veuillez réessayer."
            ),
        )

    # ── ROUTERS ───────────────────────────────────────────────────────────────
    # Chaque router est inclus avec son préfixe et ses tags Swagger

    from app.domains.auth.router import router as auth_router
    from app.domains.contributions.router import router as contributions_router
    from app.domains.members.router import router as members_router
    from app.domains.notifications.router import router as notifications_router
    from app.domains.treasury.router import router as treasury_router

    prefix = settings.api_prefix  # "/api/v1"

    app.include_router(
        auth_router,
        prefix=f"{prefix}/auth",
        tags=["Authentification"],
    )
    app.include_router(
        members_router,
        prefix=f"{prefix}/members",
        tags=["Membres"],
    )
    app.include_router(
        treasury_router,
        prefix=f"{prefix}/treasury",
        tags=["Caisse"],
    )
    app.include_router(
        contributions_router,
        prefix=f"{prefix}/contributions",
        tags=["Cotisations"],
    )
    app.include_router(
        notifications_router,
        prefix=f"{prefix}/notifications",
        tags=["Notifications"],
    )

    # ── HEALTH CHECK ──────────────────────────────────────────────────────────

    @app.get("/health", tags=["Système"])
    async def health_check():
        """
        Route de vérification que l'API tourne.
        Utilisée par Docker et les services de monitoring.
        Doit répondre < 100ms sans requête BDD.
        """
        return {
            "status": "ok",
            "version": settings.app_version,
            "environment": settings.environment,
        }

    return app


# Instance de l'application — importée par uvicorn
# uvicorn app.main:app
app = create_app()