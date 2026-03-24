"""
Orchestrateur SerpAPI : keyword → SerpAPI → rss_articles (URL only) → pipeline unifié
+ Journalisation de chaque collecte dans collection_log
"""
import hashlib
import time
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models.rss_article import RssArticle
from app.models.revue import RevueKeyword, Keyword  # noqa: F401 (used in type hint)
from app.services.serpapi_service import serpapi_service, TBS_TO_DELTA


class CollectorService:

    async def collect_for_revue(
        self,
        revue_id: uuid.UUID,
        db: AsyncSession,
        tbs: str = "qdr:d",
        num_results: int = 100,
        engine: str = "google_news",
        gl: str = "ma",
        sort_by: str = "date",
        safe_search: bool = True,
        language_override: str | None = None,  # si défini, force la langue pour tous les keywords
        trigger: str = "manual",
        keyword_ids: list[uuid.UUID] | None = None,  # None = tous les keywords actifs
    ) -> dict:
        """Lance la collecte pour tous les mots clés actifs d'une revue."""
        # Si tbs non fourni (None), on utilise les 24 dernières heures par défaut
        tbs = tbs or "qdr:d"
        start_ts = time.time()
        result = {"collected": 0, "errors": 0, "duplicates": 0, "filtered_old": 0, "articles_found": []}

        from app.models.revue import Revue
        from app.models.client import Client
        from sqlalchemy.orm import selectinload
        revue = await db.get(Revue, revue_id)
        revue_name = revue.name if revue else "Unknown"

        # Résolution du nom client pour contextualiser la pertinence NLP
        client_name: str | None = None
        if revue and revue.client_id:
            client = await db.get(Client, revue.client_id)
            if client:
                client_name = client.name

        stmt = (
            select(RevueKeyword, Keyword)
            .join(Keyword, RevueKeyword.keyword_id == Keyword.id)
            .where(RevueKeyword.revue_id == revue_id, Keyword.is_active == True)
        )
        if keyword_ids:
            stmt = stmt.where(Keyword.id.in_(keyword_ids))
        rows = await db.execute(stmt)
        revue_keywords = rows.all()

        for revue_kw, keyword in revue_keywords:
            kw_result = await self.collect_for_keyword(
                revue_id=revue_id,
                keyword=keyword,
                revue_kw=revue_kw,
                tbs=tbs,
                num_results=num_results,
                engine=engine,
                gl=gl,
                sort_by=sort_by,
                safe_search=safe_search,
                language_override=language_override,
                client_name=client_name,
                db=db,
            )
            result["collected"]      += kw_result["collected"]
            result["errors"]         += kw_result["errors"]
            result["duplicates"]     += kw_result["duplicates"]
            result["filtered_old"]   += kw_result.get("filtered_old", 0)
            result["articles_found"] += kw_result.get("articles_found", [])

        duration_ms = int((time.time() - start_ts) * 1000)
        await self._save_log(
            db=db,
            revue_id=revue_id,
            revue_name=revue_name,
            trigger=trigger,
            tbs=tbs,
            result=result,
            duration_ms=duration_ms,
            engine=engine,
            gl=gl,
            language=language_override or "fr",
            sort_by=sort_by,
            num_results=num_results,
            safe_search=safe_search,
        )
        return result

    async def collect_for_keyword(
        self,
        revue_id: uuid.UUID,
        keyword: Keyword,
        tbs: str,
        db: AsyncSession,
        revue_kw: "RevueKeyword | None" = None,
        num_results: int = 100,
        engine: str = "google_news",
        gl: str = "ma",
        sort_by: str = "date",
        safe_search: bool = True,
        language_override: str | None = None,
        client_name: str | None = None,
    ) -> dict:
        result = {"collected": 0, "errors": 0, "duplicates": 0, "filtered_old": 0, "articles_found": []}

        # Résolution des params : config propre du keyword > override global > langue keyword > défaut
        effective_tbs        = (revue_kw.tbs         if revue_kw and revue_kw.tbs         else None) or tbs
        effective_gl         = (revue_kw.gl          if revue_kw and revue_kw.gl          else None) or gl
        effective_num        = (revue_kw.num_results  if revue_kw and revue_kw.num_results  else None) or num_results
        effective_sort       = (revue_kw.sort_by      if revue_kw and revue_kw.sort_by      else None) or sort_by
        effective_safe       = (revue_kw.safe_search  if revue_kw and revue_kw.safe_search is not None else None)
        if effective_safe is None:
            effective_safe = safe_search
        # Langue : config keyword > language_override slot > langue propre keyword
        effective_lang       = (revue_kw.language     if revue_kw and revue_kw.language     else None) \
                               or language_override or keyword.language or "fr"

        # Requête : utilise query (booléenne) si définie, sinon term
        search_query = keyword.query or keyword.term
        search_results = await serpapi_service.search(
            keyword=search_query,
            language=effective_lang,
            tbs=effective_tbs,
            num_results=effective_num,
            engine=engine,
            gl=effective_gl,
            sort_by=effective_sort,
            safe_search=effective_safe,
        )

        # ── Pré-calcul du seuil de date (utilise le tbs effectif du keyword) ──
        _date_cutoff: datetime | None = None
        if effective_tbs in TBS_TO_DELTA:
            _date_cutoff = datetime.now(timezone.utc) - TBS_TO_DELTA[effective_tbs]

        # ── Set de déduplication intra-run (même URL via 2 mots-clés différents)
        seen_hashes: set[str] = set()

        for sr in search_results:
            url_hash = hashlib.sha256(sr.url.encode()).hexdigest()

            # ── 1. Déduplication intra-run ─────────────────────────────────────
            if url_hash in seen_hashes:
                result["duplicates"] += 1
                continue
            seen_hashes.add(url_hash)

            # ── 2. Déduplication inter-runs (URL déjà en rss_articles)
            existing = await db.execute(
                select(RssArticle.id).where(RssArticle.url_hash == url_hash).limit(1)
            )
            if existing.scalar_one_or_none():
                result["duplicates"] += 1
                continue

            # ── 3. Post-filtre par date (filet de sécurité) ───────────────────
            # Google filtre déjà via as_qdr côté serveur.
            # On ne rejette plus les articles sans date (tbm=nws en retourne peu).
            # On rejette uniquement les articles dont la date est clairement trop ancienne.
            _serp_date = serpapi_service.parse_serp_date(sr.serp_date)
            if _date_cutoff and _serp_date is not None:
                serp_utc = (
                    _serp_date.replace(tzinfo=timezone.utc)
                    if _serp_date.tzinfo is None
                    else _serp_date.astimezone(timezone.utc)
                )
                # Marge de 2h pour compenser les décalages de parsing
                if serp_utc < (_date_cutoff - timedelta(hours=2)):
                    result["filtered_old"] += 1
                    continue

            # ── 4. Sauvegarder URL dans rss_articles → pipeline unifié ─────────
            # source_id=None : SerpAPI n'a pas de MediaSource associée
            # published_at depuis Google News comme hint (sera confirmée par l'extracteur)
            db.add(RssArticle(
                source_id=None,
                url=sr.url,
                url_hash=url_hash,
                collection_method="serpapi",
                status="pending",
                published_at=_serp_date,
            ))
            result["collected"] += 1
            result["articles_found"].append({
                "url":         sr.url,
                "title":       sr.title,
                "source":      sr.source_name,
                "date":        sr.serp_date,
                "keyword":     keyword.term,
            })

        await db.commit()

        await db.execute(
            update(RevueKeyword)
            .where(RevueKeyword.revue_id == revue_id, RevueKeyword.keyword_id == keyword.id)
            .values(last_run_at=datetime.now(timezone.utc))
        )
        await db.commit()
        return result

    async def _save_log(
        self,
        db: AsyncSession,
        revue_id: uuid.UUID,
        revue_name: str,
        trigger: str,
        tbs: str | None,
        result: dict,
        duration_ms: int,
        # Paramètres SerpAPI
        engine: str = "google_news",
        gl: str = "ma",
        language: str = "fr",
        sort_by: str = "date",
        num_results: int = 100,
        safe_search: bool = True,
    ) -> None:
        from app.models.collection_log import CollectionLog
        from app.services.serpapi_service import TBS_TO_AS_QDR
        status = (
            "success" if result["errors"] == 0 else
            ("partial" if result["collected"] > 0 else "error")
        )
        log = CollectionLog(
            revue_id=revue_id,
            revue_name=revue_name,
            trigger=trigger,
            tbs=tbs,
            collected=result["collected"],
            errors=result["errors"],
            duplicates=result["duplicates"],
            filtered_old=result.get("filtered_old", 0),
            status=status,
            duration_ms=duration_ms,
            finished_at=datetime.now(timezone.utc),
            # Params SerpAPI
            engine=engine,
            gl=gl,
            language=language,
            sort_by=sort_by,
            as_qdr=TBS_TO_AS_QDR.get(tbs or "qdr:d", "d1"),
            safe_search=safe_search,
            num_results=num_results,
            # Articles trouvés (URL + titre)
            articles_found=result.get("articles_found", []),
        )
        db.add(log)
        await db.commit()


collector_service = CollectorService()
