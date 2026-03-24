from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Chargement explicite du .env depuis la racine du backend
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=True)


class Settings(BaseSettings):
    DATABASE_URL: str
    SERPAPI_KEY: str
    SECRET_KEY: str
    REDIS_URL: str = "redis://localhost:6379/0"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""   # https://openrouter.ai — Intelligence Layer
    NEWSDATA_API_KEY: str = ""     # https://newsdata.io — free tier = 200 req/jour
    FLARESOLVERR_URL: str = ""     # http://localhost:8191 — bypass Cloudflare JS Challenge
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    # Date de mise en marche du système (format ISO : "2025-03-17")
    # Articles publiés AVANT cette date ne sont jamais importés
    SYSTEM_START_DATE: str = "2025-03-01"
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = "GMS Veille <onboarding@resend.dev>"
    APP_URL: str = "http://localhost:3000"
    # URL du frontend (utilisée pour CORS) — en prod : https://ton-domaine.vercel.app
    FRONTEND_URL: str = "http://localhost:3000"


settings = Settings()
