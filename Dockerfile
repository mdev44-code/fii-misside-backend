# ── Stage 1 : base commune ────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    POETRY_NO_INTERACTION=1 \
    POETRY_VENV_IN_PROJECT=1 \
    POETRY_CACHE_DIR=/tmp/poetry_cache

# Installe Poetry
RUN pip install poetry==1.8.3

# Copie uniquement les fichiers de dépendances
COPY pyproject.toml poetry.lock* ./


# ── Stage 2 : développement ───────────────────────────────────────────────────
FROM base AS development

# Installe TOUTES les dépendances dans /app/.venv
RUN poetry install --no-root && rm -rf $POETRY_CACHE_DIR

# Ajoute le venv au PATH → alembic, uvicorn... utilisables directement
ENV PATH="/app/.venv/bin:$PATH"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ── Stage 3 : production ──────────────────────────────────────────────────────
FROM base AS production

RUN poetry install --only=main --no-root && rm -rf $POETRY_CACHE_DIR

ENV PATH="/app/.venv/bin:$PATH"

COPY . .

RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]