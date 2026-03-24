"""
Scraper Service — extraction d'articles.

Stratégie en deux passes :
  1. newspaper4k (rapide, sans JS) — suffit pour la plupart des sites occidentaux
  2. Playwright (Chromium headless) — fallback pour les sites JS-rendered (React/Next.js)
     comme Hespress, Goud.ma, Akhbarona, etc.

Le browser Playwright est partagé (singleton) pour éviter la surcharge de lancement.
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse, quote, urlunparse

import newspaper
import trafilatura

from app.models.article import ScrapingError

logger = logging.getLogger(__name__)

# Pool de threads dédié au scraping synchrone (newspaper4k)
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="newspaper")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _encode_url(url: str) -> str:
    """Encode les caractères non-ASCII (arabe, etc.) dans le chemin de l'URL."""
    parsed = urlparse(url)
    encoded_path = quote(parsed.path, safe='/')
    encoded_query = quote(parsed.query, safe='=&')
    return urlunparse(parsed._replace(path=encoded_path, query=encoded_query))


def _make_config() -> newspaper.Config:
    """Crée une config newspaper4k fraîche par article (pas de shared state)."""
    config = newspaper.Config()
    config.browser_user_agent = _USER_AGENT
    config.request_timeout = 20
    config.fetch_images = True
    config.memorize_articles = False  # pas de cache disque
    return config


@dataclass
class ScrapedContent:
    success: bool
    title: str | None = None
    content: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    image_url: str | None = None
    meta_description: str | None = None
    error: ScrapingError | None = None
    error_detail: str | None = None


class ScraperService:

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._browser_lock = asyncio.Lock()

    # ─── Passe 1 : newspaper4k ──────────────────────────────────────────────

    async def scrape(self, url: str, language: str = "fr") -> ScrapedContent:
        """
        Scrape un article.
        1. Newspaper4k (rapide, HTTP simple)
        2. Playwright (JS-rendered, SPAs)
        3. FlareSolverr (Cloudflare JS Challenge — si configuré)
        """
        url = _encode_url(url)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, self._scrape_sync, url, language)

        # Fallback Playwright pour les pages JS-rendered
        if not result.success and result.error == ScrapingError.js_rendered:
            logger.info(f"newspaper4k → JS, fallback Playwright pour {url}")
            result = await self._scrape_playwright(url, language)

        # Fallback FlareSolverr si Playwright bloqué (403 Cloudflare)
        if not result.success and result.error in (ScrapingError.paywall, ScrapingError.other):
            from app.services.flaresolverr_service import fetch_via_flaresolverr
            logger.info(f"Playwright bloqué, fallback FlareSolverr pour {url}")
            html = await fetch_via_flaresolverr(url)
            if html:
                fs_result = await loop.run_in_executor(_executor, self._parse_html, url, html, language)
                if fs_result.success:
                    result = fs_result

        return result

    def _scrape_sync(self, url: str, language: str = "fr") -> ScrapedContent:
        """Extraction synchrone newspaper4k — tourne dans un thread dédié."""
        try:
            article = newspaper.Article(url, config=_make_config(), language=language)
            article.download()
            article.parse()

            content = article.text

            # Contenu insuffisant → probablement page JS ou paywall
            if not content or len(content) < 100:
                return ScrapedContent(
                    success=False,
                    error=ScrapingError.js_rendered,
                    error_detail="Contenu vide — page JS ou paywall",
                )

            return self._build_result(article)

        except Exception as e:
            return ScrapedContent(
                success=False,
                error=self._classify_error(str(e)),
                error_detail=str(e)[:300],
            )

    def _parse_html(self, url: str, html: str, language: str = "fr") -> ScrapedContent:
        """
        Parse un HTML déjà rendu (fourni par Playwright).
        Utilise trafilatura pour le corps du texte (plus robuste pour l'arabe et les SPAs),
        et newspaper4k pour les métadonnées (image, auteur, date).
        """
        try:
            # 1. Extraction du texte via trafilatura
            content = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )

            if not content or len(content) < 100:
                return ScrapedContent(
                    success=False,
                    error=ScrapingError.js_rendered,
                    error_detail="Contenu vide après rendu Playwright",
                )

            # 2. Métadonnées via trafilatura
            meta = trafilatura.extract_metadata(html, default_url=url)
            title = meta.title if meta else None
            author = meta.author if meta else None
            image_url = meta.image if meta else None
            meta_description = meta.description if meta else None

            # Date de publication
            published_at = None
            if meta and meta.date:
                try:
                    from dateutil.parser import parse as parse_date
                    published_at = parse_date(meta.date)
                except Exception:
                    pass

            # 3. Fallback newspaper4k pour les champs manquants
            if not title or not image_url:
                try:
                    config = _make_config()
                    np_article = newspaper.Article(url, config=config, language=language)
                    np_article.download(input_html=html)
                    np_article.parse()
                    if not title:
                        title = np_article.title or None
                    if not image_url:
                        image_url = np_article.top_image or None
                    if not author and np_article.authors:
                        author = np_article.authors[0]
                    if not published_at and np_article.publish_date:
                        published_at = np_article.publish_date
                except Exception:
                    pass

            if meta_description and len(meta_description) < 5:
                meta_description = None

            return ScrapedContent(
                success=True,
                title=title,
                content=content,
                author=author,
                published_at=published_at,
                image_url=image_url,
                meta_description=meta_description,
            )

        except Exception as e:
            return ScrapedContent(
                success=False,
                error=self._classify_error(str(e)),
                error_detail=str(e)[:300],
            )

    # ─── Passe 2 : Playwright headless ─────────────────────────────────────

    async def _get_browser(self):
        """Retourne le browser Playwright partagé (le crée si nécessaire)."""
        async with self._browser_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                logger.info("Playwright browser lancé")
        return self._browser

    async def _scrape_playwright(self, url: str, language: str = "fr") -> ScrapedContent:
        """Scraping via Playwright + stealth — pour les sites JS et Cloudflare."""
        try:
            browser = await self._get_browser()
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True,
            )
            page = await context.new_page()

            # Appliquer playwright-stealth pour masquer les indices d'automatisation
            try:
                from playwright_stealth import Stealth
                await Stealth().apply_stealth_async(page)
            except Exception:
                pass

            # Bloquer uniquement les médias lourds (pas les stylesheets — certains sites
            # détectent leur absence et servent une page anti-bot différente)
            async def _block(route):
                if route.request.resource_type in ("image", "font", "media"):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", _block)

            response = await page.goto(url, timeout=30_000, wait_until="domcontentloaded")

            # Vérifier le statut HTTP
            if response and response.status in (401, 402, 403):
                await context.close()
                return ScrapedContent(
                    success=False,
                    error=ScrapingError.paywall,
                    error_detail=f"HTTP {response.status}",
                )

            # Laisser le JS s'exécuter (délai plus long pour les SPAs et Cloudflare challenge)
            await asyncio.sleep(4)

            html = await page.content()
            await context.close()

            # Parser le HTML rendu
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(_executor, self._parse_html, url, html, language)

        except Exception as e:
            err_str = str(e).lower()
            logger.warning(f"Playwright scraping failed — {url}: {err_str[:200]}")
            if "timeout" in err_str:
                return ScrapedContent(success=False, error=ScrapingError.timeout, error_detail=str(e)[:300])
            return ScrapedContent(success=False, error=ScrapingError.other, error_detail=str(e)[:300])

    # ─── Utilitaires ────────────────────────────────────────────────────────

    @staticmethod
    def _build_result(article: newspaper.Article) -> ScrapedContent:
        author: str | None = article.authors[0] if article.authors else None
        published_at: datetime | None = article.publish_date
        image_url: str | None = article.top_image or None
        meta_description: str | None = article.meta_description or None
        if meta_description and len(meta_description) < 5:
            meta_description = None

        return ScrapedContent(
            success=True,
            title=article.title or None,
            content=article.text,
            author=author,
            published_at=published_at,
            image_url=image_url,
            meta_description=meta_description,
        )

    @staticmethod
    def _classify_error(err_str: str) -> ScrapingError:
        err = err_str.lower()
        if "timed out" in err or "timeout" in err:
            return ScrapingError.timeout
        if "status code 404" in err or "404" in err:
            return ScrapingError.not_found
        if any(code in err for code in ("402", "403", "401", "paywall")):
            return ScrapingError.paywall
        # Cloudflare détecté par newspaper4k → tenter Playwright+stealth
        if "cloudflare" in err or "cf-mitigated" in err or "just a moment" in err:
            return ScrapingError.js_rendered
        return ScrapingError.other


scraper_service = ScraperService()
