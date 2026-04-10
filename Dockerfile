# ── Stage 1 : base commune ────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Installe Poetry (uniquement pour exporter les deps)
RUN pip install poetry==1.8.3

# Copie uniquement les fichiers de dépendances
COPY pyproject.toml poetry.lock* ./


# ── Stage 2 : développement ───────────────────────────────────────────────────
FROM base AS development

# Exporte les deps en requirements.txt et installe avec pip directement
# → pip installe dans le Python système, PATH déjà correct
RUN poetry config virtualenvs.create false \
    && poetry install --no-root \
    && rm -rf /tmp/poetry_cache

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


# ── Stage 3 : production ──────────────────────────────────────────────────────
FROM base AS production

RUN poetry config virtualenvs.create false \
    && poetry install --only=main --no-root \
    && rm -rf /tmp/poetry_cache

COPY . .

RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]