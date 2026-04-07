"""
database/session.py — Gestion des connexions à PostgreSQL.

Concepts clés :
- Engine    : la connexion physique à PostgreSQL (adresse, port, credentials)
- Session   : une "transaction" avec la BDD. Tu accumules des opérations
              (add, delete, update) et tu les envoies toutes en une fois (commit)
- Pool      : un ensemble de connexions pré-ouvertes et réutilisables.
              Ouvrir/fermer une connexion est lent → on garde un "pool" prêt.

Analogie : l'engine = le restaurant, la session = ta commande en cours,
le pool = les tables disponibles.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# create_async_engine crée le moteur de connexion async.
# pool_pre_ping=True : avant d'utiliser une connexion du pool, vérifie
# qu'elle est encore active (évite les erreurs si PostgreSQL a redémarré)
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=settings.debug,  # si True, affiche chaque requête SQL dans les logs
)

# ── Session factory ───────────────────────────────────────────────────────────
# async_sessionmaker : une "usine" qui crée des sessions à la demande.
# expire_on_commit=False : après un commit, les objets Python restent
# utilisables sans déclencher de nouvelles requêtes SQL.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,  # on gère les commits manuellement
    autoflush=False,   # on contrôle quand les changements sont envoyés à la BDD
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency FastAPI — injectée dans chaque route qui a besoin de la BDD.

    Usage dans un router :
        @router.get("/membres")
        async def liste(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Member))
            ...

    Le mot-clé 'yield' fait de cette fonction un générateur :
    - Tout ce qui est AVANT yield = code exécuté AVANT la route
    - Le yield donne la session à la route
    - Tout ce qui est APRÈS yield = code exécuté APRÈS la route (cleanup)

    Le bloc try/except garantit que :
    - Si la route se termine normalement → commit (on sauvegarde)
    - Si une exception est levée → rollback (on annule tout)
    - Dans tous les cas → la session est fermée (pas de fuite mémoire)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session          # ← la route reçoit la session ici
            await session.commit() # ← succès : on sauvegarde en BDD
        except Exception:
            await session.rollback()  # ← erreur : on annule tout
            raise                     # ← on relaie l'exception pour que FastAPI la gère
        finally:
            await session.close()  # ← toujours fermé, même en cas d'erreur