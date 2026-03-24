"""
GDELT DOC 2.0 API — Découverte de sources + collecte d'articles par keywords.

Deux usages :
  1. discover_new_domains() → trouve des domaines d'actualité marocains non encore en DB
  2. fetch_articles_for_keyword() → articles matchant un keyword, via sources déjà en DB
"""
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# Langues GDELT → nos codes langue
_LANG_MAP = {
    "arabic": "ar",
    "french": "fr",
    "english": "en",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str | None:
    try:
        return urlparse(url).netloc.lower().replace("www.", "") or None
    except Exception:
        return None


def _parse_gdelt_date(seendate: str) -> datetime | None:
    """Parse GDELT seendate format : '20260317T120000Z'"""
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


# ── Requête GDELT ─────────────────────────────────────────────────────────────

async def search_gdelt(
    query: str,
    maxrecords: int = 50,
    timespan: str = "24h",       # 15min | 1h | 6h | 24h | 1week
    sourcecountry: str = "MA",   # Maroc
    sourcelang: str | None = None,  # "arabic" | "french" | None (= toutes)
) -> list[dict]:
    """
    Requête GDELT DOC 2.0 API.
    Retourne liste d'articles : {url, title, seendate, domain, language, socialimage}
    """
    params: dict = {
        "query": query,
        "mode": "artlist",
        "maxrecords": maxrecords,
        "timespan": timespan,
        "sourcecountry": sourcecountry,
        "format": "json",
    }
    if sourcelang:
        params["sourcelang"] = sourcelang

    import asyncio
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(GDELT_DOC_API, params=params)
                if r.status_code == 429:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"[gdelt] rate limit, retry dans {wait}s")
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                return data.get("articles") or []
        except Exception as e:
            logger.warning(f"[gdelt] erreur requête '{query}' (tentative {attempt+1}): {e}")
            if attempt < 2:
                await asyncio.sleep(5)
    return []


# ── Découverte de nouvelles sources ──────────────────────────────────────────

async def discover_new_domains(
    existing_domains: set[str],
    query: str = "Maroc OR Morocco OR المغرب",
    maxrecords: int = 250,
) -> list[dict]:
    """
    Cherche des domaines d'actualité marocains actifs non encore dans notre DB.

    Returns:
        Liste de dicts {domain, sample_url, sample_title, language, count}
        triés par fréquence d'apparition.
    """
    articles = await search_gdelt(query, maxrecords=maxrecords, timespan="24h")

    # Agréger par domaine
    domains: dict[str, dict] = {}
    for art in articles:
        domain = _extract_domain(art.get("url", ""))
        if not domain or domain in existing_domains:
            continue
        if domain not in domains:
            domains[domain] = {
                "domain": domain,
                "sample_url": art.get("url", ""),
                "sample_title": art.get("title", ""),
                "language": _LANG_MAP.get((art.get("language") or "").lower(), "ar"),
                "count": 0,
            }
        domains[domain]["count"] += 1

    # Trier par fréquence (le plus actif en premier)
    return sorted(domains.values(), key=lambda x: x["count"], reverse=True)


# ── Collecte d'articles par keyword ──────────────────────────────────────────

async def fetch_articles_for_keyword(
    keyword_term: str,
    known_domains: set[str],
    hours: int = 6,
) -> list[dict]:
    """
    Cherche les articles GDELT correspondant à un keyword.
    Ne retourne que les articles dont le domaine est déjà dans notre DB
    (pour maintenir la traçabilité source → article).

    Returns:
        Liste de dicts prêts à être insérés comme RssArticle.
        Chaque dict contient : source_domain, url, url_hash, title,
        image_url, published_at, detected_language
    """
    timespan = f"{hours}h" if hours <= 24 else f"{hours // 24}d"
    articles = await search_gdelt(keyword_term, maxrecords=75, timespan=timespan)

    results = []
    for art in articles:
        url = art.get("url", "")
        if not url:
            continue
        domain = _extract_domain(url)
        if not domain or domain not in known_domains:
            continue  # Ignore les sources inconnues

        title = (art.get("title") or "").strip()
        if not title:
            continue

        lang_raw = (art.get("language") or "").lower()
        results.append({
            "source_domain": domain,
            "url": url,
            "url_hash": _url_hash(url),
            "title": title[:1024],
            "image_url": art.get("socialimage") or None,
            "published_at": _parse_gdelt_date(art.get("seendate", "")),
            "detected_language": _LANG_MAP.get(lang_raw, "ar"),
        })

    return results
