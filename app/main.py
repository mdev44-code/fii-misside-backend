from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

from app.config import settings
from app.shared.exceptions import AppException
from app.shared.response import error_response


@asynccontextmanager
async def lifespan(app: FastAPI):

    from app.infrastructure.cache.redis import init_redis
    await init_redis()

    from app.infrastructure.scheduler.jobs import start_scheduler
    start_scheduler()

    yield

    from app.infrastructure.scheduler.jobs import stop_scheduler
    stop_scheduler()

    from app.infrastructure.cache.redis import close_redis
    await close_redis()


# Origines autorisées — lues depuis .env + Angular dev ajouté explicitement
_ALLOWED_ORIGINS: set[str] = set(settings.cors_origins) | {
    "http://localhost:4200",
    "http://127.0.0.1:4200",
}


class CORSMiddleware(BaseHTTPMiddleware):
    """
    Middleware CORS custom — remplace CORSMiddleware de Starlette qui a un bug
    avec allow_credentials=True sur les versions récentes (>=0.28).
    """

    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")

        if request.method == "OPTIONS":
            response = StarletteResponse(status_code=200)
            if origin in _ALLOWED_ORIGINS:
                response.headers["Access-Control-Allow-Origin"]      = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"]     = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                response.headers["Access-Control-Allow-Headers"]     = "Authorization, Content-Type, Accept, X-Requested-With"
                response.headers["Access-Control-Max-Age"]           = "600"
                response.headers["Vary"]                             = "Origin"
            return response

        response = await call_next(request)
        if origin in _ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"]      = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"]                             = "Origin"
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="API de gestion d'association villageoise",
        docs_url="/api/docs"        if not settings.is_production else None,
        redoc_url="/api/redoc"      if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    app.add_middleware(CORSMiddleware)

    @app.exception_handler(AppException)
    async def app_exception_handler(
        request: Request,
        exc: AppException,
    ) -> JSONResponse:
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
        if settings.debug:
            raise exc

        return JSONResponse(
            status_code=500,
            content=error_response(
                "Une erreur interne est survenue. Veuillez réessayer."
            ),
        )

    from app.domains.auth.router          import router as auth_router
    from app.domains.contributions.router import router as contributions_router
    from app.domains.members.router       import router as members_router
    from app.domains.notifications.router import router as notifications_router
    from app.domains.projects.router      import router as projects_router
    from app.domains.treasury.router      import router as treasury_router
    from app.domains.postes.router import router as postes_router

    prefix = settings.api_prefix

    app.include_router(auth_router,          prefix=f"{prefix}/auth",          tags=["Authentification"])
    app.include_router(members_router,       prefix=f"{prefix}/members",       tags=["Membres"])
    app.include_router(projects_router,      prefix=f"{prefix}/projects",      tags=["Projets"])
    app.include_router(treasury_router,      prefix=f"{prefix}/treasury",      tags=["Caisse"])
    app.include_router(contributions_router, prefix=f"{prefix}/contributions", tags=["Cotisations"])
    app.include_router(notifications_router, prefix=f"{prefix}/notifications", tags=["Notifications"])
    app.include_router(
    postes_router,
    prefix=f"{prefix}/postes",
    tags=["Organigramme — Postes"],
    )

    @app.get("/health", tags=["Système"])
    async def health_check():
        return {
            "status": "ok",
            "version": settings.app_version,
            "environment": settings.environment,
        }

    return app


app = create_app()