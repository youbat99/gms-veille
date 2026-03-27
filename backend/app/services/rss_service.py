import base64
import hashlib
import re
import asyncio
import aiohttp
import feedparser
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

# trafilatura — importé ici pour éviter le chargement à chaque appel
try:
    import trafilatura
    from trafilatura.metadata import extract_metadata as _traf_meta
    _TRAF_OK = True
except ImportError:
    _TRAF_OK = False

from app.models.media_source import MediaSource
from app.models.rss_article import RssArticle

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",  # brotli supporté via package Brotli
    "Cache-Control": "no-cache",
}
TIMEOUT = aiohttp.ClientTimeout(total=15)

RSS_PATHS = [
    "/feed", "/rss", "/feed/", "/rss.xml", "/feed.xml", "/atom.xml",
    "/?feed=rss2", "/feed/rss2", "/rss/", "/feeds/",
]

# Chemins de sitemaps d'actualités (Google News Sitemap Protocol)
SITEMAP_NEWS_PATHS = [
    "/sitemap-news.xml",
    "/news-sitemap.xml",
    "/sitemap_news.xml",
    "/sitemap-google-news.xml",
    "/googlenewssitemap.xml",
    "/sitemap/news.xml",
]


def make_hash(url: str) -> str:
    return hashlib.sha256(url.strip().encode()).hexdigest()


def decode_google_news_url(url: str) -> str:
    """
    Décode une URL Google News RSS (base64/protobuf) vers l'URL réelle de l'article.
    Ex: https://news.google.com/rss/articles/CBMi... → https://fnh.ma/article/...
    Retourne l'URL originale si le décodage échoue ou si ce n'est pas une URL Google News.
    """
    if "news.google.com" not in url:
        return url
    try:
        match = re.search(r"/articles/([^?#]+)", url)
        if not match:
            return url
        b64 = match.group(1)
        b64 += "=" * (-len(b64) % 4)  # padding
        raw = base64.urlsafe_b64decode(b64)
        # L'URL réelle est encodée dans les bytes protobuf — on la cherche directement
        decoded = raw.decode("latin-1")
        found = re.findall(r"https?://[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+", decoded)
        if found:
            # La plus longue URL est généralement l'URL réelle de l'article
            real = max(found, key=len).rstrip("\\")
            return real
    except Exception:
        pass
    return url


def get_logo_url(base_url: str) -> str:
    """Retourne l'URL Clearbit — le frontend fait le fallback favicon automatiquement."""
    domain = urlparse(base_url).netloc.replace("www.", "")
    return f"https://logo.clearbit.com/{domain}"


def get_favicon_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def _extract_date_from_html(html: str):
    """
    Extracteur de date universel via patterns HTML communs.
    Priorité : article:published_time meta > time[datetime] > classes CSS date.
    Retourne un datetime aware ou None.
    """
    import re as _re
    from dateutil.parser import parse as _dp
    from datetime import timezone as _tz

    candidates: list[str] = []

    # 1. <meta property="article:published_time" content="...">  (OGP — le plus fiable)
    m = _re.search(r'article:published_time["\s]+content=["\']([^"\']+)', html, _re.IGNORECASE)
    if m:
        candidates.append(m.group(1).strip())

    # 2. <meta name="date" content="..."> ou <meta name="pubdate" content="...">
    m = _re.search(r'<meta[^>]+name=["\'](?:date|pubdate|publish_date)["\'][^>]+content=["\']([^"\']+)', html, _re.IGNORECASE)
    if m:
        candidates.append(m.group(1).strip())

    # 3. <time datetime="..."> (élément HTML5 standard)
    m = _re.search(r'<time[^>]+datetime=["\']([^"\']{10,35})["\']', html, _re.IGNORECASE)
    if m:
        candidates.append(m.group(1).strip())

    # 4. JSON-LD : "datePublished": "..."
    m = _re.search(r'"datePublished"\s*:\s*"([^"]{10,35})"', html)
    if m:
        candidates.append(m.group(1).strip())

    # 5. Attribut data-date ou data-published
    m = _re.search(r'data-(?:date|published|publish-date)=["\']([^"\']{10,35})["\']', html, _re.IGNORECASE)
    if m:
        candidates.append(m.group(1).strip())

    for raw in candidates:
        try:
            dt = _dp(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            return dt
        except Exception:
            continue

    return None


def _newspaper_fetch_sync(url: str) -> dict:
    """Extraction via Newspaper4k — meilleur pour les sites FR/EN (moins de bruit)."""
    try:
        import newspaper
        article = newspaper.Article(url, language="fr")
        article.download()
        article.parse()
        result: dict = {}
        if article.text:
            result["content"] = article.text[:60_000]
        if article.title:
            result["title"] = article.title
        if article.authors:
            result["author"] = ", ".join(article.authors)[:255]
        if article.publish_date:
            from datetime import timezone as _tz
            pub = article.publish_date
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=_tz.utc)
            result["published_at_fallback"] = pub
        if article.top_image:
            result["image_url"] = article.top_image
        return result
    except Exception:
        return {}


def _traf_fetch_sync(url: str) -> dict:
    """Extraction via Trafilatura — meilleur pour les sites AR et le contenu complet."""
    if not _TRAF_OK:
        return {}
    try:
        import urllib.request as _ur
        req = _ur.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8,en;q=0.7",
        })
        with _ur.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        if not html:
            return {}
        result: dict = {}
        content = trafilatura.extract(html, include_comments=False, include_tables=False)
        if content:
            result["content"] = content[:60_000]
        meta = _traf_meta(html)
        if meta:
            if getattr(meta, "title", None):
                result["title"] = str(meta.title)[:1024]
            if meta.image:
                result["image_url"] = meta.image
            if meta.author:
                result["author"] = str(meta.author)[:255]
            if getattr(meta, "language", None):
                result["detected_language"] = str(meta.language)[:10]
        # Date : extracteur HTML universel en priorité (plus précis que Trafilatura meta)
        html_date = _extract_date_from_html(html)
        if html_date:
            result["published_at_fallback"] = html_date
        elif meta and getattr(meta, "date", None):
            try:
                from dateutil.parser import parse as _parse_date
                from datetime import timezone as _tz
                parsed = _parse_date(str(meta.date))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=_tz.utc)
                result["published_at_fallback"] = parsed
            except Exception:
                pass
        return result
    except Exception:
        return {}


def _traf_from_html(html: str) -> dict:
    """Extrait via Trafilatura depuis un HTML déjà téléchargé (ex: FlareSolverr)."""
    if not _TRAF_OK or not html:
        return {}
    try:
        result: dict = {}
        content = trafilatura.extract(html, include_comments=False, include_tables=False)
        if content:
            result["content"] = content[:60_000]
        meta = _traf_meta(html)
        if meta:
            if getattr(meta, "title", None):
                result["title"] = str(meta.title)[:1024]
            if meta.image:
                result["image_url"] = meta.image
            if meta.author:
                result["author"] = str(meta.author)[:255]
            if getattr(meta, "language", None):
                result["detected_language"] = str(meta.language)[:10]
        # Date : extracteur HTML universel en priorité
        html_date = _extract_date_from_html(html)
        if html_date:
            result["published_at_fallback"] = html_date
        elif meta and getattr(meta, "date", None):
            try:
                from dateutil.parser import parse as _parse_date
                from datetime import timezone as _tz
                parsed = _parse_date(str(meta.date))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=_tz.utc)
                result["published_at_fallback"] = parsed
            except Exception:
                pass
        return result
    except Exception:
        return {}


async def _resolve_google_news_url(url: str) -> str:
    """
    Résout une URL Google News RSS vers l'URL réelle de l'article via Playwright.
    Google News redirige via JavaScript — httpx/Trafilatura ne peuvent pas suivre cette redirection.
    Retourne l'URL originale si la résolution échoue.
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                # Attendre que la navigation soit complète (après le redirect JS)
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)  # laisser le redirect JS s'exécuter
                final_url = page.url
                # S'assurer qu'on a bien quitté news.google.com
                if "news.google.com" not in final_url and final_url != url:
                    return final_url
            finally:
                await browser.close()
    except Exception:
        pass
    return url


async def enrich_article(url: str, language: str = "ar") -> dict:
    """
    Extrait le contenu d'un article selon la langue de la source :
    - AR → Trafilatura (meilleur support arabe)  avec fallback Newspaper4k
    - FR/EN → Newspaper4k (texte plus propre)    avec fallback Trafilatura
    - Si les deux échouent (ex: Cloudflare) → FlareSolverr (si configuré)
    """
    # Résoudre l'URL Google News → URL réelle avant toute extraction
    original_url = url
    if "news.google.com" in url:
        url = await _resolve_google_news_url(url)

    # Fix B — Encoder les caractères non-ASCII (arabe, amazigh, cyrillique…)
    # Même logique que scraper_service.scrape() — évite HTTP 400 sur URLs arabes
    from app.services.scraper_service import _encode_url as _enc
    url = _enc(url)

    loop = asyncio.get_event_loop()

    if language == "ar":
        primary, fallback = _traf_fetch_sync, _newspaper_fetch_sync
    else:
        primary, fallback = _newspaper_fetch_sync, _traf_fetch_sync

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, primary, url),
            timeout=15.0,
        )
        # Fallback si le primaire n'a pas extrait le contenu OU le titre
        if not result.get("content") or not result.get("title"):
            fallback_result = await asyncio.wait_for(
                loop.run_in_executor(None, fallback, url),
                timeout=12.0,
            )
            if not result.get("title") and fallback_result.get("title"):
                result["title"] = fallback_result["title"]
            if not result.get("content") and fallback_result.get("content"):
                result["content"] = fallback_result["content"]
            if not result.get("author") and fallback_result.get("author"):
                result["author"] = fallback_result["author"]
            if not result.get("image_url") and fallback_result.get("image_url"):
                result["image_url"] = fallback_result["image_url"]

        # Fallback Playwright si toujours pas de contenu (site JS-rendered : React, Next.js…)
        if not result.get("content"):
            from app.services.scraper_service import scraper_service
            pw_result = await scraper_service._scrape_playwright(url, language)
            if pw_result.success:
                if not result.get("title") and pw_result.title:
                    result["title"] = pw_result.title
                if pw_result.content:
                    result["content"] = pw_result.content
                if not result.get("author") and pw_result.author:
                    result["author"] = pw_result.author
                if not result.get("image_url") and pw_result.image_url:
                    result["image_url"] = pw_result.image_url

        # Fallback FlareSolverr si toujours pas de contenu (Cloudflare bloqué)
        if not result.get("content"):
            from app.services.flaresolverr_service import fetch_via_flaresolverr
            html = await fetch_via_flaresolverr(url)
            if html:
                fs_result = _traf_from_html(html)
                if not result.get("title") and fs_result.get("title"):
                    result["title"] = fs_result["title"]
                if fs_result.get("content"):
                    result["content"] = fs_result["content"]
                if not result.get("author") and fs_result.get("author"):
                    result["author"] = fs_result["author"]
                if not result.get("image_url") and fs_result.get("image_url"):
                    result["image_url"] = fs_result["image_url"]
                if fs_result.get("detected_language"):
                    result["detected_language"] = fs_result["detected_language"]

        # Inclure l'URL résolue si elle a changé (Google News → URL réelle)
        if url != original_url:
            result["resolved_url"] = url
        return result
    except (asyncio.TimeoutError, Exception):
        return {}


def extract_image(entry) -> str | None:
    """Extrait l'image principale d'une entrée RSS."""
    # 1. media:content
    media = getattr(entry, "media_content", None)
    if media and isinstance(media, list):
        for m in media:
            url = m.get("url", "")
            if url and any(ext in url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                return url
        if media[0].get("url"):
            return media[0]["url"]

    # 2. media:thumbnail
    thumb = getattr(entry, "media_thumbnail", None)
    if thumb and isinstance(thumb, list) and thumb[0].get("url"):
        return thumb[0]["url"]

    # 3. enclosure (podcast/image attachée)
    enclosures = getattr(entry, "enclosures", [])
    for enc in enclosures:
        if enc.get("type", "").startswith("image/"):
            return enc.get("href") or enc.get("url")

    # 4. Chercher une img dans le summary HTML
    summary_html = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary_html, re.IGNORECASE)
    if match:
        src = match.group(1)
        if src.startswith("http"):
            return src

    return None


def get_base_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def parse_date(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime(*val[:6])
            except Exception:
                pass
    for field in ("published", "updated"):
        val = getattr(entry, field, None)
        if val:
            try:
                return parsedate_to_datetime(val)
            except Exception:
                pass
    return None


async def _test_rss_url(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=TIMEOUT, headers=HEADERS, ssl=False, allow_redirects=True) as resp:
            if resp.status == 200:
                content = await resp.text()
                if any(tag in content[:500] for tag in ["<rss", "<feed", "<channel", "<?xml"]):
                    return url
    except Exception:
        pass
    return None


async def _find_rss_in_html(session: aiohttp.ClientSession, base_url: str) -> str | None:
    try:
        async with session.get(base_url, timeout=TIMEOUT, headers=HEADERS, ssl=False) as resp:
            if resp.status == 200:
                html = await resp.text()
                patterns = [
                    r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/rss\+xml["\']',
                    r'<link[^>]+type=["\']application/atom\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                ]
                for pattern in patterns:
                    match = re.search(pattern, html, re.IGNORECASE)
                    if match:
                        href = match.group(1)
                        return href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
    except Exception:
        pass
    return None


async def _test_sitemap_url(session: aiohttp.ClientSession, url: str) -> str | None:
    """Vérifie qu'une URL est un sitemap d'actualités valide (contient <url> et <loc>)."""
    try:
        async with session.get(url, timeout=TIMEOUT, headers=HEADERS, ssl=False, allow_redirects=True) as resp:
            if resp.status == 200:
                content = await resp.text(errors="replace")
                # Un sitemap news contient <urlset>, <url>, <loc> ET idéalement <news:
                if "<urlset" in content and "<loc>" in content:
                    return url
    except Exception:
        pass
    return None


async def detect_rss(url: str) -> dict | None:
    """
    Détecte automatiquement la source d'un site — par ordre de priorité :
      1. RSS/Atom natif (via <link> HTML ou chemins standards)
      2. News Sitemap (Google News Sitemap Protocol)
      3. Playwright (fallback — crawl direct de la homepage)
    Retourne {"rss_url": ..., "rss_type": ..., "crawl_method": ...}
    """
    base_url = get_base_url(url) if not url.startswith("http") else url
    if "://" not in base_url:
        base_url = "https://" + base_url

    headers_wp = {
        **HEADERS,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. HTML <link rel="alternate"> pour RSS/Atom
        rss = await _find_rss_in_html(session, base_url)
        if rss:
            verified = await _test_rss_url(session, rss)
            if verified:
                return {"rss_url": verified, "rss_type": "natif", "crawl_method": "rss"}

        # 2. Chemins RSS standards
        for path in RSS_PATHS:
            candidate = base_url.rstrip("/") + path
            result = await _test_rss_url(session, candidate)
            if result:
                return {"rss_url": result, "rss_type": "natif", "crawl_method": "rss"}

        # 3. News Sitemap (WordPress, Yoast SEO, XML Sitemap plugin…)
        for path in SITEMAP_NEWS_PATHS:
            candidate = base_url.rstrip("/") + path
            result = await _test_sitemap_url(session, candidate)
            if result:
                return {"rss_url": result, "rss_type": "sitemap", "crawl_method": "sitemap"}

        # 4. Essayer de trouver le sitemap index et en extraire un sitemap news ou post
        import re as _re
        from dateutil.parser import parse as _dp
        for index_path in ["/sitemap.xml", "/wp-sitemap.xml", "/sitemap_index.xml"]:
            try:
                async with session.get(
                    base_url.rstrip("/") + index_path,
                    timeout=aiohttp.ClientTimeout(total=8),
                    headers=HEADERS, ssl=False, allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        continue
                    idx_content = await resp.text(errors="replace")

                    # 4a. Chercher un sitemap news dans l'index (Google News Sitemap Protocol)
                    news_matches = _re.findall(
                        r'<loc>\s*(https?://[^\s<]+(?:news|news-sitemap)[^\s<]*)\s*</loc>',
                        idx_content, _re.IGNORECASE
                    )
                    for nm in news_matches[:3]:
                        result = await _test_sitemap_url(session, nm.strip())
                        if result:
                            return {"rss_url": result, "rss_type": "sitemap", "crawl_method": "sitemap"}

                    # 4b. Chercher le sitemap post le plus récent (WordPress standard)
                    # Les articles récents sont dans le sub-sitemap avec le numéro le plus élevé
                    if "<sitemapindex" in idx_content:
                        import xml.etree.ElementTree as _ET
                        try:
                            _root = _ET.fromstring(idx_content)
                            _ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                            post_sitemaps: list[tuple[int, str, str]] = []
                            for sm in _root.findall("s:sitemap", _ns):
                                loc = sm.findtext("s:loc", namespaces=_ns) or ""
                                lm  = sm.findtext("s:lastmod", namespaces=_ns) or ""
                                if not _re.search(r'post-sitemap', loc, _re.IGNORECASE):
                                    continue
                                # Extraire le numéro de la fin : post-sitemap278.xml → 278
                                # post-sitemap.xml (sans numéro) → 0
                                num_m = _re.search(r'post-sitemap(\d+)\.xml', loc, _re.IGNORECASE)
                                num = int(num_m.group(1)) if num_m else 0
                                post_sitemaps.append((num, lm, loc))

                            if post_sitemaps:
                                # Trier par lastmod DESC, puis numéro DESC (articles récents = dernier sitemap)
                                post_sitemaps.sort(key=lambda x: (x[1], x[0]), reverse=True)
                                best_loc = post_sitemaps[0][2]
                                result = await _test_sitemap_url(session, best_loc.strip())
                                if result:
                                    return {"rss_url": result, "rss_type": "post_sitemap", "crawl_method": "sitemap"}
                        except Exception:
                            pass
            except Exception:
                pass

        # 5. BeautifulSoup — requête HTTP simple, pas besoin de JS
        bs_result = await _detect_via_requests(session, base_url)
        if bs_result:
            return bs_result

        # 6. FlareSolverr — site Cloudflare protégé
        flare_result = await _detect_via_flaresolverr(base_url)
        if flare_result:
            return flare_result

        # 7. Fallback → Playwright (SPA React/Vue, dernier recours)
        return {"rss_url": base_url, "rss_type": "playwright", "crawl_method": "playwright"}


_ARTICLE_URL_RE = re.compile(
    r'/(\d{4})/(\d{2})/(\d{2})/|'       # /2026/03/27/
    r'/(\d{4})-(\d{2})-(\d{2})/|'       # /2026-03-27/
    r'/article[s]?/|/news/|/actu/|'     # segments courants
    r'/post[s]?/|/actualit|/fil-info',
    re.IGNORECASE,
)

_CLOUDFLARE_RE = re.compile(
    r'checking your browser|just a moment|cf-browser-verification|'
    r'enable javascript|cloudflare ray id',
    re.IGNORECASE,
)

_SPA_RE = re.compile(
    r'<div\s+id=["\'](?:root|app|__next)["\']>\s*</div>|'
    r'<div\s+id=["\'](?:root|app|__next)["\']/>',
    re.IGNORECASE,
)


def _extract_date_from_url(url: str):
    """Extrait une date depuis l'URL si elle contient /YYYY/MM/DD/ ou /YYYY-MM-DD/."""
    from datetime import timezone as _tz
    m = re.search(r'/(\d{4})[/-](\d{2})[/-](\d{2})/', url)
    if m:
        try:
            from datetime import datetime as _dt
            return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_tz.utc)
        except Exception:
            pass
    return None


async def _detect_via_requests(
    session: aiohttp.ClientSession, base_url: str
) -> dict | None:
    """
    Étape 5 de detect_rss : teste si le site répond en HTML statique exploitable.
    Retourne crawl_method='requests' si OK, None sinon.
    """
    try:
        async with session.get(
            base_url, timeout=aiohttp.ClientTimeout(total=10),
            headers=HEADERS, ssl=False, allow_redirects=True
        ) as resp:
            if resp.status in (403, 503, 429):
                return None
            if resp.status != 200:
                return None
            html = await resp.text(errors="replace")

        if not html or len(html) < 500:
            return None

        # SPA React/Vue vide → Playwright nécessaire
        if _SPA_RE.search(html):
            return None

        # Cloudflare challenge → FlareSolverr
        if _CLOUDFLARE_RE.search(html):
            return None

        # Site HTML statique exploitable : pas SPA, pas Cloudflare, répond 200
        # → on classe directement en "requests", le crawleur extraira ce qu'il peut
        # (on n'exige plus de patterns d'URL spécifiques — trop restrictif pour les sites MA)
        return {"rss_url": base_url, "rss_type": "html", "crawl_method": "requests"}

    except Exception:
        pass
    return None


async def _detect_via_flaresolverr(base_url: str) -> dict | None:
    """
    Étape 6 de detect_rss : tente de bypasser Cloudflare via FlareSolverr local.
    Retourne crawl_method='flaresolverr' si OK, None sinon.
    """
    from app.core.config import settings
    flare_url = getattr(settings, "FLARESOLVERR_URL", "http://localhost:8191")
    if not flare_url:
        return None
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(
                f"{flare_url}/v1",
                json={"cmd": "request.get", "url": base_url, "maxTimeout": 20000},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("status") != "ok":
                    return None
                html = data.get("solution", {}).get("response", "")
                if not html or len(html) < 500:
                    return None
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                links = [
                    a["href"] for a in soup.find_all("a", href=True)
                    if _ARTICLE_URL_RE.search(a["href"])
                ]
                if len(links) >= 3:
                    return {"rss_url": base_url, "rss_type": "html", "crawl_method": "flaresolverr"}
    except Exception:
        pass
    return None


async def crawl_source_requests(
    session: aiohttp.ClientSession, source: "MediaSource"
) -> list[dict]:
    """
    Crawle une source via requête HTTP simple + BeautifulSoup.
    Extrait les liens d'articles depuis la homepage, filtre par date (max 48h).
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from bs4 import BeautifulSoup

    cutoff = _dt.now(_tz.utc) - _td(hours=48)
    articles = []

    try:
        async with session.get(
            source.base_url, timeout=aiohttp.ClientTimeout(total=15),
            headers=HEADERS, ssl=False, allow_redirects=True
        ) as resp:
            if resp.status != 200:
                return []
            html = await resp.text(errors="replace")
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        # URL absolue
        if href.startswith("/"):
            href = source.base_url.rstrip("/") + href
        elif not href.startswith("http"):
            continue

        # Doit ressembler à un article
        if not _ARTICLE_URL_RE.search(href):
            continue

        # Déduplique
        url_hash = make_hash(href)
        if url_hash in seen:
            continue
        seen.add(url_hash)

        # Date depuis l'URL
        pub_date = _extract_date_from_url(href)

        # Filtre 48h si date trouvée
        if pub_date and pub_date < cutoff:
            continue

        # Titre depuis le texte du lien ou balise parente
        title = a.get_text(strip=True)
        if not title:
            parent = a.find_parent(["h1", "h2", "h3", "h4", "article"])
            if parent:
                title = parent.get_text(strip=True)[:200]

        articles.append({
            "url": href,
            "url_hash": url_hash,
            "rss_title": title[:500] if title else None,
            "rss_pub_date": pub_date,
            "rss_image": None,
        })

    return articles[:50]  # max 50 articles par crawl


async def crawl_source_flaresolverr(source: "MediaSource") -> list[dict]:
    """
    Crawle une source protégée Cloudflare via FlareSolverr.
    Même logique que crawl_source_requests mais via le proxy local.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from bs4 import BeautifulSoup
    from app.core.config import settings

    flare_url = getattr(settings, "FLARESOLVERR_URL", "http://localhost:8191")
    cutoff = _dt.now(_tz.utc) - _td(hours=48)
    articles = []

    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(
                f"{flare_url}/v1",
                json={"cmd": "request.get", "url": source.base_url, "maxTimeout": 20000},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if data.get("status") != "ok":
                    return []
                html = data.get("solution", {}).get("response", "")
    except Exception:
        return []

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        if href.startswith("/"):
            href = source.base_url.rstrip("/") + href
        elif not href.startswith("http"):
            continue
        if not _ARTICLE_URL_RE.search(href):
            continue

        url_hash = make_hash(href)
        if url_hash in seen:
            continue
        seen.add(url_hash)

        pub_date = _extract_date_from_url(href)
        if pub_date and pub_date < cutoff:
            continue

        title = a.get_text(strip=True)
        if not title:
            parent = a.find_parent(["h1", "h2", "h3", "h4", "article"])
            if parent:
                title = parent.get_text(strip=True)[:200]

        articles.append({
            "url": href,
            "url_hash": url_hash,
            "rss_title": title[:500] if title else None,
            "rss_pub_date": pub_date,
            "rss_image": None,
        })

    return articles[:50]


async def _fetch_og(session: aiohttp.ClientSession, url: str) -> dict:
    """Extrait og:title / og:description / og:image / article:published_time depuis le HTML."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10),
                               headers=HEADERS, ssl=False, allow_redirects=True) as resp:
            if resp.status != 200:
                return {}
            html = await resp.text(errors="replace")
    except Exception:
        return {}

    result: dict = {}
    for prop, key in [
        ("og:title",                 "title"),
        ("og:description",           "summary"),
        ("og:image",                 "image_url"),
        ("article:published_time",   "published_at"),
    ]:
        m = re.search(
            rf'<meta[^>]+property=["\']{ re.escape(prop) }["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE
        ) or re.search(
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{ re.escape(prop) }["\']',
            html, re.IGNORECASE
        )
        if m:
            result[key] = m.group(1).strip()

    # Fallback title → <title>
    if not result.get("title"):
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if m:
            result["title"] = m.group(1).strip()

    return result


async def _fetch_all_post_sitemaps(
    session: aiohttp.ClientSession,
    base_url: str,
    cutoff: datetime,
    max_sitemaps: int = 5,
) -> list[str]:
    """
    Depuis l'index sitemap du site, retourne les post-sitemaps
    dont lastmod >= cutoff (triés du plus récent au plus ancien).
    Limité à max_sitemaps par cycle pour éviter le rate-limiting.
    Le rattrapage historique se fait progressivement sur plusieurs cycles.
    """
    import xml.etree.ElementTree as ET
    from dateutil.parser import parse as _dp
    from datetime import timezone as _tz

    NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    for index_path in ["/wp-sitemap.xml", "/sitemap.xml", "/sitemap_index.xml"]:
        try:
            async with session.get(
                base_url.rstrip("/") + index_path,
                timeout=aiohttp.ClientTimeout(total=8),
                headers=HEADERS, ssl=False, allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    continue
                content = await resp.text(errors="replace")
                if "<sitemapindex" not in content:
                    continue

                root = ET.fromstring(content.encode("utf-8", errors="replace"))
                found: list[tuple[int, str, str]] = []

                for sm in root.findall("s:sitemap", NS):
                    loc = (sm.findtext("s:loc", namespaces=NS) or "").strip()
                    lm  = (sm.findtext("s:lastmod", namespaces=NS) or "").strip()
                    if not loc or not re.search(r'post[-_]sitemap', loc, re.IGNORECASE):
                        continue
                    # Ignorer les sous-sitemaps clairement trop anciens
                    if lm:
                        try:
                            lm_dt = _dp(lm)
                            if lm_dt.tzinfo is None:
                                lm_dt = lm_dt.replace(tzinfo=_tz.utc)
                            if lm_dt < cutoff:
                                continue  # Tout le sous-sitemap est antérieur au cutoff
                        except Exception:
                            pass  # Pas de date valide → inclure par précaution
                    # Extraire le numéro pour trier (plus grand = plus récent)
                    num_m = re.search(r'post[-_]sitemap(\d+)', loc, re.IGNORECASE)
                    num = int(num_m.group(1)) if num_m else 0
                    found.append((num, lm, loc))

                if found:
                    # Trier du plus récent au plus ancien
                    found.sort(key=lambda x: (x[1], x[0]), reverse=True)
                    # Limiter le nombre de sitemaps par cycle pour éviter le rate-limiting
                    # Le rattrapage historique se fait progressivement sur plusieurs cycles
                    return [loc for _, _, loc in found[:max_sitemaps]]
        except Exception:
            continue

    return []


async def crawl_source_sitemap(session: aiohttp.ClientSession, source: MediaSource) -> list[dict]:
    """
    Crawle un News Sitemap (Google News Sitemap Protocol) ou un post-sitemap WordPress standard.

    - rss_type == 'sitemap'      → Google News Sitemap : titre + date dans le XML
    - rss_type == 'post_sitemap' → Yoast/RankMath post sitemap :
        * Cutoff dynamique basé sur source.last_crawled_at (pas de 48h fixe)
        * Premier crawl : remonte jusqu'à SYSTEM_START_DATE sur TOUS les sous-sitemaps
        * Garantit 0 article manqué depuis le démarrage du système
    """
    import xml.etree.ElementTree as ET
    from dateutil.parser import parse as _parse_date
    from datetime import timezone, timedelta
    from app.core.config import settings

    NS = {
        "s":     "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news":  "http://www.google.com/schemas/sitemap-news/0.9",
        "image": "http://www.google.com/schemas/sitemap-image/1.1",
    }
    is_post_sitemap = getattr(source, "rss_type", "sitemap") == "post_sitemap"

    # ── Calcul de la date de coupure ────────────────────────────────────
    try:
        system_start = _parse_date(settings.SYSTEM_START_DATE).replace(tzinfo=timezone.utc)
    except Exception:
        system_start = datetime(2025, 1, 1, tzinfo=timezone.utc)

    if is_post_sitemap:
        if source.last_crawled_at:
            # Overlap de 2h pour couvrir les articles publiés pendant le dernier crawl
            last = source.last_crawled_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            cutoff = max(last - timedelta(hours=2), system_start)
        else:
            # Premier crawl → remonter jusqu'à la date de mise en marche
            cutoff = system_start
    else:
        # News sitemap Google : les articles sont déjà limités aux ~48h par le protocole
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    # ── Découverte des sous-sitemaps (post_sitemap uniquement) ──────────
    if is_post_sitemap:
        base = f"{urlparse(source.rss_url).scheme}://{urlparse(source.rss_url).netloc}"
        sitemap_urls = await _fetch_all_post_sitemaps(session, base, cutoff)
        if not sitemap_urls:
            # Fallback : utiliser l'URL stockée directement
            sitemap_urls = [source.rss_url]
    else:
        sitemap_urls = [source.rss_url]

    # ── Crawl de chaque sous-sitemap ────────────────────────────────────
    all_candidates: list[dict] = []

    for i, sitemap_url in enumerate(sitemap_urls):
        # Petit délai entre les sous-sitemaps pour éviter le rate-limiting
        if i > 0:
            await asyncio.sleep(1.0)
        try:
            async with session.get(
                sitemap_url, timeout=TIMEOUT, headers=HEADERS, ssl=False, allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    continue
                raw = await resp.read()

            root = ET.fromstring(raw)

            for url_el in root.findall("s:url", NS):
                loc = url_el.findtext("s:loc", namespaces=NS) or ""
                loc = loc.strip()
                if not loc:
                    continue

                # Ignorer la homepage
                parsed_path = urlparse(loc).path
                if not parsed_path or parsed_path == "/":
                    continue

                # Date de publication (news: ou lastmod)
                pub_date: datetime | None = None
                pub_date_str = (
                    url_el.findtext("news:news/news:publication_date", namespaces=NS) or
                    url_el.findtext("s:lastmod", namespaces=NS)
                )
                if pub_date_str:
                    try:
                        pub_date = _parse_date(pub_date_str.strip())
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass

                # Filtrer par date (post_sitemap : cutoff dynamique ; news sitemap : 48h)
                if pub_date is None or pub_date < cutoff:
                    if is_post_sitemap:
                        continue  # Ignorer les articles trop anciens

                # Titre (dispo dans les news sitemaps ; absent dans les post sitemaps)
                title = (
                    url_el.findtext("news:news/news:title", namespaces=NS) or
                    url_el.findtext("news:title", namespaces=NS) or
                    ""
                )
                title = title.strip()[:1024]

                # Image (sitemap image extension)
                image = url_el.findtext("image:image/image:loc", namespaces=NS)
                if image:
                    image = image.strip()

                all_candidates.append({
                    "url":         loc,
                    "url_hash":    make_hash(loc),
                    "rss_pub_date": pub_date,   # date+heure depuis le sitemap
                })

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[sitemap] {source.name} / {sitemap_url}: {e}")

    return all_candidates


async def crawl_source_playwright(source: MediaSource) -> list[dict]:
    """
    Crawle la homepage d'un site sans RSS natif via Playwright (headless Chromium).
    Extrait les liens d'articles directement depuis le DOM de la page.
    """
    from playwright.async_api import async_playwright

    articles = []
    base_domain = urlparse(source.base_url).netloc.replace("www.", "")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36",
                    locale="fr-FR",
                    extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8"},
                )
                page = await context.new_page()
                # Stealth mode pour contourner Cloudflare JS challenge
                try:
                    from playwright_stealth import Stealth
                    await Stealth().apply_stealth_async(page)
                except Exception:
                    pass
                await page.goto(source.base_url, timeout=25000, wait_until="domcontentloaded")
                # Attendre un peu pour le JS dynamique (+ délai Cloudflare challenge)
                await page.wait_for_timeout(3000)

                # Extraction JS : liens d'articles avec titre, résumé, image
                items = await page.evaluate("""
                    (baseDomain) => {
                        const results = [];
                        const seen = new Set();
                        const SKIP_PATHS = new Set([
                            'tag','tags','category','categorie','categories',
                            'auteur','author','authors','page','contact',
                            'about','apropos','publicite','advertise',
                            'search','recherche','login','signin',
                        ]);

                        for (const link of document.querySelectorAll('a[href]')) {
                            let href;
                            try { href = new URL(link.href).href; } catch { continue; }

                            // Même domaine seulement (match exact, pas les sous-domaines)
                            let linkDomain;
                            try { linkDomain = new URL(href).hostname.replace('www.',''); } catch { continue; }
                            if (linkDomain !== baseDomain) continue;

                            // URL : au moins 1 segment de chemin
                            const parts = new URL(href).pathname.split('/').filter(Boolean);
                            if (parts.length < 1) continue;
                            if (SKIP_PATHS.has(parts[0].toLowerCase())) continue;

                            if (seen.has(href)) continue;
                            seen.add(href);

                            // Titre : texte du lien ou heading du parent
                            let title = link.innerText?.trim() || '';
                            const parent = link.closest('article, [class*="card"], [class*="item"], [class*="post"], li');
                            if (title.length < 15 && parent) {
                                const h = parent.querySelector('h1,h2,h3,h4');
                                if (h) title = h.innerText?.trim() || '';
                            }
                            if (title.length < 10) continue;

                            // Résumé
                            let summary = null;
                            if (parent) {
                                const p = parent.querySelector('p');
                                if (p) summary = p.innerText?.trim()?.substring(0, 500) || null;
                            }

                            // Image
                            let image = null;
                            if (parent) {
                                const img = parent.querySelector('img[src]');
                                if (img && img.src && img.src.startsWith('http')) image = img.src;
                            }

                            results.push({ url: href, title: title.substring(0, 1024), summary, image });
                            if (results.length >= 60) break;
                        }
                        return results;
                    }
                """, base_domain)

                for item in items:
                    url = item.get("url", "").strip()
                    if not url:
                        continue
                    articles.append({
                        "url":      url,
                        "url_hash": make_hash(url),
                    })

            finally:
                await browser.close()

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[playwright] {source.name}: {e}")

    return articles


async def crawl_source(session: aiohttp.ClientSession, source: MediaSource) -> list[dict]:
    """
    Récupère les URLs depuis un flux RSS.
    Sauvegarde aussi l'image RSS en fallback (image_url) — utilisée si l'extracteur
    ne trouve pas d'image sur la page de l'article.
    """
    urls = []
    try:
        async with session.get(source.rss_url, timeout=TIMEOUT, headers=HEADERS, ssl=False) as resp:
            if resp.status != 200:
                return []
            content = await resp.read()

        feed = feedparser.parse(content)
        for entry in feed.entries:
            url = entry.get("link", "").strip()
            if not url:
                continue
            url = decode_google_news_url(url)  # résout les URLs news.google.com → URL réelle
            item: dict = {"url": url, "url_hash": make_hash(url)}
            # Titre RSS — fallback si l'extracteur ne trouve rien sur la page
            rss_title = (entry.get("title") or "").strip() or None
            if rss_title:
                item["rss_title"] = rss_title[:1024]
            # Image RSS — fallback si l'extracteur ne trouve rien sur la page
            rss_image = extract_image(entry)
            if rss_image:
                item["rss_image"] = rss_image
            # Date de publication RSS — source la plus fiable (avec heure exacte)
            rss_pub_date = parse_date(entry)
            if rss_pub_date:
                item["rss_pub_date"] = rss_pub_date
            urls.append(item)
    except Exception:
        pass
    return urls


# ── Architecture rolling crawl ─────────────────────────────────────────

async def _crawl_and_save(db: AsyncSession, source: "MediaSource") -> dict:
    """
    Noyau commun : crawle une source déjà chargée, déduplique, sauvegarde.
    Appelé par crawl_next_source (1 source) et crawl_source_by_id (parallèle).
    """
    from app.models.source_crawl_log import SourceCrawlLog

    stats = {"source": source.name, "fetched": 0, "saved": 0, "duplicates": 0}
    method = getattr(source, "crawl_method", "rss")

    if method == "playwright":
        articles = await crawl_source_playwright(source)
    elif method == "flaresolverr":
        articles = await crawl_source_flaresolverr(source)
    elif method == "sitemap":
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            articles = await crawl_source_sitemap(http_session, source)
    elif method == "requests":
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            articles = await crawl_source_requests(http_session, source)
    else:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            articles = await crawl_source(http_session, source)

    for art in articles:
        stats["fetched"] += 1
        existing = await db.execute(
            select(RssArticle.id).where(RssArticle.url_hash == art["url_hash"])
        )
        if existing.scalar_one_or_none():
            stats["duplicates"] += 1
            continue
        try:
            db.add(RssArticle(
                source_id=source.id,
                url=art["url"],
                url_hash=art["url_hash"],
                collection_method=method,
                status="pending",
                title=art.get("rss_title"),
                image_url=art.get("rss_image"),
                published_at=art.get("rss_pub_date"),
            ))
            await db.flush()
            stats["saved"] += 1
        except Exception:
            await db.rollback()
            stats["duplicates"] += 1

    source.last_crawled_at = datetime.now(timezone.utc)

    # Mise à jour du score adaptatif (articles/jour sur 30 derniers jours)
    try:
        from datetime import timedelta as _td
        from sqlalchemy import func as _func
        cutoff_30 = datetime.now(timezone.utc) - _td(days=30)
        count_result = await db.execute(
            select(_func.count(RssArticle.id))
            .where(RssArticle.source_id == source.id)
            .where(RssArticle.created_at >= cutoff_30)
        )
        total_30d = count_result.scalar() or 0
        apd = round(total_30d / 30, 2)
        source.articles_per_day = apd
        if apd >= 10:
            source.crawl_interval_minutes = 60
        elif apd >= 3:
            source.crawl_interval_minutes = 180
        elif apd >= 0.5:
            source.crawl_interval_minutes = 720
        else:
            source.crawl_interval_minutes = 1440
    except Exception:
        pass

    db.add(SourceCrawlLog(
        source_id=source.id,
        trigger="rolling",
        new_articles=stats["saved"],
        total_found=stats["fetched"],
        duplicates=stats["duplicates"],
        duration_ms=None,
    ))
    await db.commit()
    return stats


async def crawl_next_source(db: AsyncSession) -> dict:
    """
    Rolling crawl : prend la source active dont last_crawled_at est le plus ancien
    (jamais crawlée = priorité absolue), la crawle, sauvegarde les articles bruts.
    L'enrichissement trafilatura est délégué à enrich_pending_batch().
    """
    result = await db.execute(
        select(MediaSource)
        .where(MediaSource.is_active == True)
        .order_by(MediaSource.last_crawled_at.asc().nullsfirst())
        .limit(1)
    )
    source = result.scalar_one_or_none()
    if not source:
        return {"source": None, "fetched": 0, "saved": 0, "duplicates": 0}
    return await _crawl_and_save(db, source)


async def crawl_source_by_id(source_id, db: AsyncSession) -> dict:
    """
    Crawl une source par son UUID (utilisé pour le crawl parallèle).
    La source doit avoir été réservée (last_crawled_at mis à jour) avant l'appel.
    """
    result = await db.execute(
        select(MediaSource).where(MediaSource.id == source_id)
    )
    source = result.scalar_one_or_none()
    if not source:
        return {"source": str(source_id), "fetched": 0, "saved": 0, "duplicates": 0}
    return await _crawl_and_save(db, source)


_NOISE_PATTERNS = re.compile(
    r'(?:lire aussi|voir aussi|à lire aussi|sur le même sujet'
    r'|اقرأ أيضاً?|اقرأ ايضا|انظر أيضاً?'
    r'|related articles?|you may also like)[^\n]*',
    re.IGNORECASE,
)

def _is_date_only(dt) -> bool:
    """True si la date est minuit UTC (= date sans heure, ex: lastmod sitemap)."""
    if dt is None:
        return True
    from datetime import timezone as _tz
    dt_utc = dt.astimezone(_tz.utc) if dt.tzinfo else dt
    return dt_utc.hour == 0 and dt_utc.minute == 0 and dt_utc.second == 0


def _clean_content(text: str) -> str:
    """Supprime les sections parasites (Lire aussi, اقرأ أيضاً, etc.)."""
    if not text:
        return text
    # Tronquer dès le premier bloc "Lire aussi"
    m = _NOISE_PATTERNS.search(text)
    if m:
        text = text[:m.start()].strip()
    return text


# Fix C — Backoff exponentiel pour les retries (6 tentatives sur 24h)
# Index = retry_count au moment de l'échec (0-based)
_RETRY_DELAYS = [
    timedelta(minutes=30),   # retry 1  → 30 min après 1ère tentative
    timedelta(hours=1),      # retry 2  → 1h
    timedelta(hours=3),      # retry 3  → 3h
    timedelta(hours=6),      # retry 4  → 6h
    timedelta(hours=12),     # retry 5  → 12h
    timedelta(hours=24),     # retry 6  → 24h → mort définitive
]
_MAX_RETRIES = len(_RETRY_DELAYS)  # 6


async def enrich_pending_batch(db: AsyncSession, batch_size: int = 5) -> dict:
    """
    Worker d'extraction : prend les articles status='pending',
    choisit l'extracteur selon la langue de la source (Newspaper4k FR/EN, Trafilatura AR),
    extrait title/content/author/date/image/langue. Met à jour status='extracted' ou 'failed'.
    """
    from sqlalchemy import case as sa_case
    result = await db.execute(
        select(RssArticle, MediaSource)
        .outerjoin(MediaSource, RssArticle.source_id == MediaSource.id)  # LEFT JOIN — SerpAPI n'a pas de source
        .where(RssArticle.status == "pending")
        .order_by(
            sa_case((RssArticle.collection_method == "serpapi", 0), else_=1),  # SerpAPI en priorité
            RssArticle.collected_at.asc(),
        )
        .limit(batch_size)
    )
    rows = result.all()

    if not rows:
        return {"enriched": 0}

    sem = asyncio.Semaphore(batch_size)

    async def _do_enrich(art: RssArticle, source: MediaSource | None):
        async with sem:
            from app.services.dedup_service import compute_fingerprint
            try:
                lang = source.language if source else "fr"  # SerpAPI → FR par défaut
                enrichment = await enrich_article(art.url, language=lang)

                # Mettre à jour l'URL si elle a été résolue (Google News → URL réelle)
                if enrichment.get("resolved_url"):
                    new_hash = make_hash(enrichment["resolved_url"])
                    # Vérifier si l'URL réelle existe déjà dans rss_articles (crawlée par RSS)
                    conflict = await db.execute(
                        select(RssArticle.id).where(
                            RssArticle.url_hash == new_hash,
                            RssArticle.id != art.id
                        ).limit(1)
                    )
                    if conflict.scalar_one_or_none():
                        # Doublon : l'URL réelle est déjà en base → supprimer ce doublon SerpAPI
                        await db.delete(art)
                        return
                    art.url = enrichment["resolved_url"]
                    art.url_hash = new_hash

                if not enrichment.get("content") and not enrichment.get("title"):
                    # Rien extrait — marquer comme failed avec backoff exponentiel
                    art.status = "failed"
                    art.enriched_at = datetime.now(timezone.utc)
                    retry_count = (art.retry_count or 0) + 1
                    art.retry_count = retry_count
                    if retry_count < _MAX_RETRIES:
                        art.extraction_error = "no_content"
                        art.retry_after = datetime.now(timezone.utc) + _RETRY_DELAYS[retry_count - 1]
                    else:
                        art.extraction_error = "no_content_final"  # Fix D
                        art.retry_after = None  # mort définitive
                    return

                # Titre — priorité : page réelle > fallback vide
                if enrichment.get("title"):
                    import html as _html
                    t = _html.unescape(enrichment["title"]).strip()
                    t_clean = re.sub(r'\s*[-|]\s*[^-|]{3,60}$', '', t).strip()
                    art.title = (t_clean or t)[:1024]

                # Détection page 404 / erreur — titre typique des pages d'erreur
                _404_PATTERNS = (
                    "غير موجودة", "الصفحة غير", "page not found", "404",
                    "introuvable", "not found", "page introuvable",
                    "error 404", "خطأ 404", "لا توجد", "page doesn't exist",
                )
                if art.title and any(p in art.title.lower() for p in _404_PATTERNS):
                    art.status = "failed"
                    art.extraction_error = "page_404"
                    art.retry_after = None  # pas de retry — URL morte
                    art.enriched_at = datetime.now(timezone.utc)
                    return

                # Texte complet nettoyé
                if enrichment.get("content"):
                    art.content = _clean_content(enrichment["content"])[:60_000]

                # Image — l'extracteur a priorité, sinon on garde le fallback RSS
                if enrichment.get("image_url"):
                    art.image_url = enrichment["image_url"]
                # art.image_url peut déjà être rempli depuis le flux RSS (crawl_source)

                # Auteur
                if enrichment.get("author"):
                    art.author = enrichment["author"]

                # Date de publication — priorité à l'heure exacte extraite de la page
                # RSS avec heure exacte → conservée
                # Sitemap avec lastmod date-only (00:00:00 UTC) → Trafilatura override
                if enrichment.get("published_at_fallback") and _is_date_only(art.published_at):
                    art.published_at = enrichment["published_at_fallback"]

                # Langue
                if enrichment.get("detected_language"):
                    art.detected_language = enrichment["detected_language"]

                # Fingerprint SimHash (near-dedup au moment de l'extraction)
                art.content_fingerprint = compute_fingerprint(art.title or "", art.content)

                art.status = "extracted"
                art.enriched_at = datetime.now(timezone.utc)

            except Exception as e:
                err = str(e).lower()
                art.status = "failed"
                art.extraction_error = str(e)[:500]
                art.enriched_at = datetime.now(timezone.utc)
                art.retry_count = (art.retry_count or 0) + 1
                if art.retry_count < 3:
                    # paywall/cloudflare → retry 6h, timeout/réseau → retry 30min
                    is_paywall = any(k in err for k in ("paywall", "403", "cloudflare", "access denied"))
                    delay = timedelta(hours=6 if is_paywall else 0, minutes=0 if is_paywall else 30)
                    art.retry_after = datetime.now(timezone.utc) + delay
                else:
                    art.retry_after = None  # définitivement failed

    await asyncio.gather(*[_do_enrich(art, source) for art, source in rows])
    await db.commit()
    return {"enriched": len(rows)}


# ── Retry des articles failed ─────────────────────────────────────────

async def retry_failed_batch(db: AsyncSession, batch_size: int = 10) -> dict:
    """
    Retry intelligent des articles failed :
    - Fix A : utilise enrich_article() (chaîne complète : Trafila → Newspaper → Playwright → FlareSolverr)
    - Fix C : backoff exponentiel 30min → 1h → 3h → 6h → 12h → 24h (6 tentatives max)
    - Fix D : extraction_error = "no_content_final" à la mort définitive
    """
    from sqlalchemy import and_
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(RssArticle, MediaSource)
        .outerjoin(MediaSource, RssArticle.source_id == MediaSource.id)
        .where(
            and_(
                RssArticle.status == "failed",
                RssArticle.retry_count < _MAX_RETRIES,
                RssArticle.retry_after.isnot(None),
                RssArticle.retry_after <= now,
                RssArticle.collected_at > now - timedelta(days=7)
            )
        )
        .order_by(RssArticle.retry_after.asc())
        .limit(batch_size)
    )
    rows = result.all()
    if not rows:
        return {"retried": 0, "recovered": 0}

    recovered = 0

    async def _do_retry(art: RssArticle, source: MediaSource | None):
        nonlocal recovered
        lang = source.language if source else "fr"

        # Fix A — Utiliser enrich_article (chaîne complète avec Playwright + FlareSolverr)
        # au lieu du bare _traf_fetch_sync / _newspaper_fetch_sync sans fallback
        try:
            enrichment = await enrich_article(art.url, language=lang)
        except Exception:
            enrichment = {}

        if enrichment.get("content") or enrichment.get("title"):
            from app.services.dedup_service import compute_fingerprint
            import html as _html
            if enrichment.get("title"):
                t = _html.unescape(enrichment["title"]).strip()
                t_clean = re.sub(r'\s*[-|]\s*[^-|]{3,60}$', '', t).strip()
                art.title = (t_clean or t)[:1024]
            if enrichment.get("content"):
                art.content = _clean_content(enrichment["content"])[:60_000]
            if enrichment.get("image_url"):
                art.image_url = enrichment["image_url"]
            if enrichment.get("author"):
                art.author = enrichment["author"]
            if enrichment.get("published_at_fallback") and _is_date_only(art.published_at):
                art.published_at = enrichment["published_at_fallback"]
            # Résolution Google News → mettre à jour l'URL
            if enrichment.get("resolved_url"):
                art.url = enrichment["resolved_url"]
                art.url_hash = make_hash(enrichment["resolved_url"])
            art.content_fingerprint = compute_fingerprint(art.title or "", art.content)
            art.status = "extracted"
            art.extraction_error = None
            art.enriched_at = datetime.now(timezone.utc)
            art.retry_after = None
            recovered += 1
        else:
            # Fix C — Backoff exponentiel + Fix D — no_content_final à la mort
            retry_count = (art.retry_count or 0) + 1
            art.retry_count = retry_count
            if retry_count < _MAX_RETRIES:
                art.extraction_error = "no_content"
                art.retry_after = datetime.now(timezone.utc) + _RETRY_DELAYS[retry_count - 1]
            else:
                art.extraction_error = "no_content_final"  # Fix D — purge cohérente
                art.retry_after = None

    await asyncio.gather(*[_do_retry(art, source) for art, source in rows])
    await db.commit()
    return {"retried": len(rows), "recovered": recovered}


# ── Purge automatique des articles périmés ────────────────────────────

async def purge_old_articles(db: AsyncSession, days: int = 30) -> dict:
    """
    Supprime les articles obsolètes pour éviter que la table explose :
    - status='no_match'  → jamais matché un keyword, inutile après X jours
    - status='failed' avec extraction_error='no_content_final' → non récupérables
    Conserve tous les articles 'matched' et 'extracted' indéfiniment.
    """
    from sqlalchemy import delete, and_
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Supprimer les no_match anciens
    r1 = await db.execute(
        delete(RssArticle).where(
            and_(
                RssArticle.status == "no_match",
                RssArticle.collected_at < cutoff,
            )
        )
    )
    # Supprimer les failed définitifs anciens
    r2 = await db.execute(
        delete(RssArticle).where(
            and_(
                RssArticle.status == "failed",
                RssArticle.extraction_error == "no_content_final",
                RssArticle.collected_at < cutoff,
            )
        )
    )
    await db.commit()
    return {
        "no_match_deleted": r1.rowcount,
        "failed_deleted": r2.rowcount,
    }


# ── Crawl manuel complet (déclenché via API) ───────────────────────────

async def crawl_all_sources(db: AsyncSession) -> dict:
    """
    Crawl manuel de toutes les sources actives d'un coup (API /crawl).
    Sauvegarde les articles bruts sans enrichissement — le worker prend le relais.
    """
    result = await db.execute(select(MediaSource).where(MediaSource.is_active == True))
    sources = result.scalars().all()

    stats = {"fetched": 0, "saved": 0, "duplicates": 0}

    # Séparer par méthode
    rss_sources      = [s for s in sources if getattr(s, "crawl_method", "rss") == "rss"]
    sitemap_sources  = [s for s in sources if getattr(s, "crawl_method", "rss") == "sitemap"]
    pw_sources       = [s for s in sources if getattr(s, "crawl_method", "rss") == "playwright"]

    # RSS + Sitemap : parallèle via aiohttp
    all_results_rss: list[list[dict]] = []
    all_results_sitemap: list[list[dict]] = []

    if rss_sources or sitemap_sources:
        connector = aiohttp.TCPConnector(limit=30, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as http_session:
            if rss_sources:
                rss_tasks = [crawl_source(http_session, s) for s in rss_sources]
                all_results_rss = list(await asyncio.gather(*rss_tasks))
            if sitemap_sources:
                sm_tasks = [crawl_source_sitemap(http_session, s) for s in sitemap_sources]
                all_results_sitemap = list(await asyncio.gather(*sm_tasks))

    # Playwright : séquentiel pour éviter la surcharge
    all_results_pw: list[list[dict]] = []
    for s in pw_sources:
        pw_arts = await crawl_source_playwright(s)
        all_results_pw.append(pw_arts)

    from app.models.source_crawl_log import SourceCrawlLog
    import time as _time

    combined_sources = rss_sources + sitemap_sources + pw_sources
    all_results = all_results_rss + all_results_sitemap + all_results_pw

    for source, articles in zip(combined_sources, all_results):
        collection_method = getattr(source, "crawl_method", "rss")
        t0 = _time.monotonic()
        source_new = 0
        source_total = len(articles)
        source_dupes = 0
        for art in articles:
            stats["fetched"] += 1
            existing = await db.execute(
                select(RssArticle.id).where(RssArticle.url_hash == art["url_hash"])
            )
            if existing.scalar_one_or_none():
                stats["duplicates"] += 1
                source_dupes += 1
                continue
            try:
                db.add(RssArticle(
                    source_id=source.id,
                    url=art["url"],
                    url_hash=art["url_hash"],
                    collection_method=collection_method,
                    status="pending",
                    title=art.get("rss_title"),
                    image_url=art.get("rss_image"),
                    published_at=art.get("rss_pub_date"),
                ))
                await db.flush()
                stats["saved"] += 1
                source_new += 1
            except Exception:
                await db.rollback()
                stats["duplicates"] += 1
                source_dupes += 1
        source.last_crawled_at = datetime.now(timezone.utc)
        # Log du crawl par source
        db.add(SourceCrawlLog(
            source_id=source.id,
            trigger="scheduled",
            new_articles=source_new,
            total_found=source_total,
            duplicates=source_dupes,
            duration_ms=int((_time.monotonic() - t0) * 1000),
        ))

    await db.commit()
    return stats
