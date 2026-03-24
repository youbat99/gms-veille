"""
FlareSolverr — bypass Cloudflare JS Challenge pour les sites protégés (ex: 2m.ma).
Nécessite FlareSolverr en local : docker run -d -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
"""
import httpx
from app.core.config import settings


async def fetch_via_flaresolverr(url: str, timeout_ms: int = 30000) -> str | None:
    """
    Soumet l'URL à FlareSolverr et retourne le HTML résolu.
    Retourne None si FlareSolverr n'est pas configuré ou si la requête échoue.
    """
    if not settings.FLARESOLVERR_URL:
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000 + 5) as client:
            r = await client.post(
                f"{settings.FLARESOLVERR_URL.rstrip('/')}/v1",
                json={
                    "cmd": "request.get",
                    "url": url,
                    "maxTimeout": timeout_ms,
                },
            )
            r.raise_for_status()
            data = r.json()
            solution = data.get("solution", {})
            return solution.get("response") or None
    except Exception:
        return None


async def is_flaresolverr_available() -> bool:
    """Vérifie que FlareSolverr est actif et joignable."""
    if not settings.FLARESOLVERR_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.FLARESOLVERR_URL.rstrip('/')}/health")
            return r.status_code == 200
    except Exception:
        return False
