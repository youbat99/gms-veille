# ── Stage 1 : Build ────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Dépendances système minimales pour compiler les packages Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Installer les dépendances Python dans un dossier isolé
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2 : Runtime ──────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Copier les packages Python installés depuis le builder
COPY --from=builder /install /usr/local

# Installer Playwright + toutes ses dépendances système (chromium uniquement)
# playwright install --with-deps gère lui-même apt-get
RUN pip install --no-cache-dir playwright && \
    playwright install --with-deps chromium

# Copier le code source
COPY . .

# Port d'écoute
EXPOSE 8000

# Healthcheck — vérifié par Coolify/Docker
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Démarrage production (pas de --reload)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
