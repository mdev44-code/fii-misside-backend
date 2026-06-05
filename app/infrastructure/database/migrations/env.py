import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.infrastructure.database.models  # noqa: F401 — force l'enregistrement des tables

# ← Import critique : tes modèles + Base
from app.infrastructure.database.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ← Ici le fix : on pointe vers la metadata de tes modèles
target_metadata = Base.metadata

# ← Surcharge l'URL depuis la variable d'environnement
database_url_sync = os.getenv(
    "DATABASE_URL_SYNC", "postgresql://postgres:password@localhost:5432/fii_misside_db"
)
config.set_main_option("sqlalchemy.url", database_url_sync)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
