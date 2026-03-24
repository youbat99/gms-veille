import hashlib
import re
import httpx
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from app.core.config import settings

# Mapping tbs → timedelta — post-filtre par date réelle (filet de sécurité)
TBS_TO_DELTA: dict[str, timedelta] = {
    "qdr:h":   timedelta(hours=1),
    "qdr:h2":  timedelta(hours=2),
    "qdr:h3":  timedelta(hours=3),
    "qdr:h4":  timedelta(hours=4),
    "qdr:h6":  timedelta(hours=6),
    "qdr:h8":  timedelta(hours=8),
    "qdr:h12": timedelta(hours=12),
    "qdr:d":   timedelta(hours=25),
    "qdr:w":   timedelta(days=8),
    "qdr:m":   timedelta(days=32),
    "qdr:y":   timedelta(days=370),
}

# Mapping tbs → as_qdr (paramètre documenté pour engine=google + tbm=nws)
TBS_TO_AS_QDR: dict[str, str] = {
    "qdr:h":   "h1",
    "qdr:h2":  "h2",
    "qdr:h3":  "h3",
    "qdr:h4":  "h4",
    "qdr:h6":  "h6",
    "qdr:h8":  "h8",
    "qdr:h12": "h12",
    "qdr:d":   "d1",
    "qdr:w":   "w1",
    "qdr:m":   "m1",
    "qdr:y":   "y1",
}

GL_OPTIONS = [
    {"value": "ma", "label": "🇲🇦 Maroc"},
    {"value": "fr", "label": "🇫🇷 France"},
    {"value": "dz", "label": "🇩🇿 Algérie"},
    {"value": "tn", "label": "🇹🇳 Tunisie"},
    {"value": "eg", "label": "🇪🇬 Égypte"},
    {"value": "sa", "label": "🇸🇦 Arabie Saoudite"},
    {"value": "ae", "label": "🇦🇪 Émirats"},
    {"value": "gb", "label": "🇬🇧 Royaume-Uni"},
    {"value": "us", "label": "🇺🇸 États-Unis"},
]

# Un seul engine désormais : google + tbm=nws
ENGINE_OPTIONS = [
    {"value": "google_news", "label": "Google Actualités (tbm=nws, as_qdr)"},
]

SORT_OPTIONS = [
    {"value": "date",      "label": "Date (plus récent d'abord)"},
    {"value": "relevance", "label": "Pertinence"},
]


@dataclass
class SearchResult:
    url: str
    title: str
    source_domain: str
    snippet: str
    url_hash: str
    source_name: str = field(default="")
    serp_date: str | None = field(default=None)


class SerpAPIService:
    BASE_URL = "https://serpapi.com/search"

    async def search(
        self,
        keyword: str,
        language: str = "fr",
        tbs: str | None = "qdr:d",
        num_results: int = 100,
        engine: str = "google_news",   # conservé pour compatibilité, toujours google+tbm=nws
        gl: str = "ma",
        sort_by: str = "date",
        safe_search: bool = True,
    ) -> list[SearchResult]:
        """
        Recherche via SerpAPI — engine=google + tbm=nws.

        Pourquoi pas engine=google_news ?
        → news.google.com n'expose aucun filtre de date ni tri.
        → Confirmé "Not Planned" par SerpAPI (Issue #78, mars 2025).

        Paramètres effectifs envoyés à SerpAPI :
            engine=google, tbm=nws, q, gl, hl, as_qdr, tbs=sbd:1, num, safe
        """
        return await self._search_google_news(
            keyword=keyword,
            language=language,
            tbs=tbs,
            num_results=num_results,
            gl=gl,
            sort_by=sort_by,
            safe_search=safe_search,
        )

    async def _search_google_news(
        self,
        keyword: str,
        language: str,
        tbs: str | None,
        num_results: int,
        gl: str,
        sort_by: str,
        safe_search: bool,
    ) -> list[SearchResult]:
        """
        engine=google + tbm=nws — paramètres documentés et fonctionnels :
          - as_qdr : filtre temporel (d1=24h, h6=6h, w1=1 semaine…)
          - tbs=sbd:1 : tri par date
          - safe : SafeSearch active/off
          - num : nombre de résultats (max 100)
        """
        # as_qdr = version documentée du filtre temporel pour tbm=nws
        as_qdr = TBS_TO_AS_QDR.get(tbs or "qdr:d", "d1")

        params: dict = {
            "engine":  "google",
            "tbm":     "nws",
            "q":       keyword,
            "api_key": settings.SERPAPI_KEY,
            "hl":      language,
            "gl":      gl,
            "num":     min(num_results, 100),
            "safe":    "active" if safe_search else "off",
            "as_qdr":  as_qdr,
            "no_cache": "true",   # toujours des résultats frais
        }

        # Tri par date si demandé (sbd:1 = sort by date)
        if sort_by == "date":
            params["tbs"] = "sbd:1"

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        results = self._parse_results(data)

        # Fallback sans filtre temporel si aucun résultat (keyword très spécifique)
        if not results and as_qdr:
            fallback = {k: v for k, v in params.items() if k not in ("as_qdr", "no_cache")}
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(self.BASE_URL, params=fallback)
                response.raise_for_status()
                data = response.json()
            results = self._parse_results(data)

        return results

    def _parse_results(self, data: dict) -> list[SearchResult]:
        # tbm=nws retourne "news_results"
        items = data.get("news_results") or data.get("organic_results") or []
        results = []

        for item in items:
            url = item.get("link", "")
            if not url:
                continue

            # tbm=nws : source est une string directe
            source_raw = item.get("source", "")
            if isinstance(source_raw, dict):
                source_name = source_raw.get("name", "")
            else:
                source_name = str(source_raw) if source_raw else ""

            # date retournée par tbm=nws : texte relatif ("3 hours ago") ou absolu
            raw_date = item.get("date")

            results.append(SearchResult(
                url=url,
                title=item.get("title", ""),
                source_domain=self._extract_domain(url),
                snippet=item.get("snippet", ""),
                url_hash=self._hash_url(url),
                source_name=source_name,
                serp_date=raw_date,
            ))
        return results

    def _extract_domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return ""

    def _hash_url(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def parse_serp_date(self, date_str: str | None) -> datetime | None:
        """
        Parse les dates retournées par SerpAPI tbm=nws :
          - ISO 8601  : "2026-03-22T09:30:00+00:00"
          - Relatives : "3 hours ago", "2 days ago", "5 minutes ago"
          - Absolues  : "March 13, 2026", "Jan 5, 2026"
        """
        if not date_str:
            return None
        now = datetime.now(timezone.utc)
        s = date_str.strip()

        # ISO 8601
        try:
            parsed = datetime.fromisoformat(s)
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

        # Relatives anglaises : "3 hours ago", "5 minutes ago"
        m = re.match(r"(\d+)\s*(minute|hour|day|week|month|year)s?\s*ago", s.lower())
        if m:
            n, unit = int(m.group(1)), m.group(2)
            delta_map = {
                "minute": timedelta(minutes=n),
                "hour":   timedelta(hours=n),
                "day":    timedelta(days=n),
                "week":   timedelta(weeks=n),
                "month":  timedelta(days=n * 30),
                "year":   timedelta(days=n * 365),
            }
            return now - delta_map[unit]

        # Absolues : "March 13, 2026"
        try:
            from dateutil.parser import parse as _parse
            return _parse(date_str, fuzzy=True).replace(tzinfo=timezone.utc)
        except Exception:
            return None


serpapi_service = SerpAPIService()
