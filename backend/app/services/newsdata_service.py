"""
NewsData.io API — Collecte d'articles Maroc (AR + FR).

Free tier : 200 requêtes/jour, ~10 articles/requête.
Scheduler : IntervalTrigger(hours=6) → 4 runs/jour → ~160 req/jour.

Docs : https://newsdata.io/documentation
"""
import hashlib
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

NEWSDATA_API = "https://newsdata.io/api/1/latest"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _parse_newsdata_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


_LANG_MAP = {"arabic": "ar", "french": "fr", "english": "en"}


# ── Requête NewsData ──────────────────────────────────────────────────────────

async def fetch_morocco_news(
    api_key: str,
    query: str | None = None,
    language: str = "ar,fr",
    page_token: str | None = None,
    size: int = 10,
) -> dict:
    """
    Retourne les dernières actualités marocaines depuis NewsData.io.

    Args:
        api_key:    Clé API NewsData.io
        query:      Mot-clé de recherche (optionnel)
        language:   Langues séparées par virgule ("ar,fr" par défaut)
        page_token: Token de pagination pour la page suivante
        size:       Nombre d'articles (max 10 en free tier)

    Returns:
        {status, totalResults, results: [...], nextPage}
    """
    params: dict = {
        "apikey": api_key,
        "country": "ma",
        "language": language,
        "size": size,
    }
    if query:
        params["q"] = query
    if page_token:
        params["page"] = page_token

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(NEWSDATA_API, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning(f"[newsdata] erreur fetch: {e}")
        return {"status": "error", "results": []}


# ── Collecte par keyword ───────────────────────────────────────────────────────

async def fetch_articles_for_keyword(
    api_key: str,
    keyword_term: str,
    known_domains: set[str],
) -> list[dict]:
    """
    Cherche les articles NewsData.io pour un keyword.
    Ne retourne que les articles dont le domaine est déjà dans notre DB.

    Returns:
        Liste de dicts prêts pour RssArticle :
        {source_domain, url, url_hash, title, summary, image_url,
         published_at, detected_language}
    """
    data = await fetch_morocco_news(api_key, query=keyword_term, size=10)
    results = []

    for art in data.get("results") or []:
        url = (art.get("link") or "").strip()
        if not url:
            continue

        # Vérifier que le domaine est dans notre DB
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower().replace("www.", "")
        except Exception:
            continue

        if domain not in known_domains:
            continue

        title = (art.get("title") or "").strip()
        if not title:
            continue

        lang_raw = (art.get("language") or "ar").lower()
        results.append({
            "source_domain": domain,
            "url": url,
            "url_hash": _url_hash(url),
            "title": title[:1024],
            "summary": (art.get("description") or None),
            "image_url": art.get("image_url") or None,
            "published_at": _parse_newsdata_date(art.get("pubDate")),
            "detected_language": _LANG_MAP.get(lang_raw, lang_raw[:10]),
        })

    return results


# ── Collecte générale (sans keyword) ─────────────────────────────────────────

async def fetch_all_morocco_articles(
    api_key: str,
    known_domains: set[str],
    languages: list[str] | None = None,
) -> list[dict]:
    """
    Collecte d'articles marocains récents sans filtre keyword.
    Tourne sur AR et FR séparément pour maximiser les résultats.

    Returns:
        Liste de dicts prêts pour RssArticle
    """
    if languages is None:
        languages = ["ar", "fr"]

    all_results = []
    for lang in languages:
        data = await fetch_morocco_news(api_key, language=lang, size=10)
        for art in data.get("results") or []:
            url = (art.get("link") or "").strip()
            if not url:
                continue
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower().replace("www.", "")
            except Exception:
                continue
            if domain not in known_domains:
                continue
            title = (art.get("title") or "").strip()
            if not title:
                continue
            all_results.append({
                "source_domain": domain,
                "url": url,
                "url_hash": _url_hash(url),
                "title": title[:1024],
                "summary": art.get("description") or None,
                "image_url": art.get("image_url") or None,
                "published_at": _parse_newsdata_date(art.get("pubDate")),
                "detected_language": lang,
            })

    return all_results
