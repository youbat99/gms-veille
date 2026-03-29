"""
Pipeline : rss_articles → keyword matching → articles (pending validation)

Logique :
 - Prend les articles RSS enrichis (enriched_at IS NOT NULL) non encore matchés (matched_at IS NULL)
 - Pour chaque article, teste toutes les combinaisons (revue, keyword) actives
 - Match UNIQUEMENT dans le titre + résumé (pas dans le contenu intégral — trop de faux positifs)
 - Parser booléen avec parenthèses :
     ("A" OR "B") AND (C OR D)  →  au moins un terme de chaque groupe AND doit être présent
 - Score minimum : 65 (terme trouvé dans titre ou résumé)
 - Déduplication : pas deux Article avec même (url, revue_id)
 - Met à jour matched_at sur le rss_article une fois traité (qu'il y ait match ou non)
"""
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rss_article import RssArticle
from app.models.article import Article, ArticleStatus
from app.models.revue import Revue, RevueKeyword, Keyword


# ── Score minimum pour qu'un article soit retenu ────────────────────────────
MIN_SCORE = 40  # 90 = dans le titre, 65 = résumé/début, 40 = corps complet


# ── Normalisation du texte ───────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """
    Normalisation complète pour le matching keyword :
    - Arabe : supprime tashkeel (diacritiques), normalise alef/ya/ta marbuta
    - Latin : supprime accents
    - Minuscules, sans ponctuation
    Permet de matcher "وزارة التجهيز" même si le texte contient "وِزَارَةُ التَّجْهِيز"
    ou "Ministère" vs "Ministere".
    """
    # 1. Tashkeel arabe (diacritiques U+0610–U+061A et U+064B–U+065F)
    text = re.sub(r'[\u0610-\u061A\u064B-\u065F]', '', text)
    # 2. Normalisation alef variants → ا
    text = re.sub(r'[إأآ]', 'ا', text)
    # 3. Ya → ي  et  Ta marbuta → ه
    text = text.replace('ى', 'ي').replace('ة', 'ه')
    # 4. Accents latins (NFD → supprimer combining marks)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # 5. Minuscules
    text = text.lower()
    # 6. Ponctuation → espaces (garde alphanums latin + arabe)
    text = re.sub(r"[^\w\s\u0600-\u06ff]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains(haystack_norm: str, term: str) -> bool:
    """
    Vérifie si le terme est présent dans le haystack normalisé.
    - Terme ≤ 4 caractères → mot entier uniquement (\bterm\b) pour éviter
      les faux positifs (ex: "RN" dans "Mernissi", "RP" dans "carpe")
    - Terme > 4 caractères → sous-chaîne (plus souple pour les phrases)
    """
    t = _normalize(term)
    if not t:
        return False
    if len(t) <= 4:
        # Mot entier : le terme doit être entouré de non-alphanum ou de début/fin
        pattern = r"(?<![a-z0-9\u0600-\u06ff])" + re.escape(t) + r"(?![a-z0-9\u0600-\u06ff])"
        return bool(re.search(pattern, haystack_norm))
    return t in haystack_norm


# ── Parser booléen avec parenthèses ─────────────────────────────────────────

def _split_top_level(text: str, sep: str) -> list[str]:
    """
    Split 'text' sur 'sep' (ex: ' AND ', ' OR ') uniquement au niveau 0
    des parenthèses (ne coupe pas à l'intérieur d'un groupe parenthésé).
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    i = 0
    sep_u = sep.upper()
    while i < len(text):
        if text[i] == "(":
            depth += 1
            current.append(text[i])
        elif text[i] == ")":
            depth -= 1
            current.append(text[i])
        elif depth == 0 and text[i:i + len(sep)].upper() == sep_u:
            parts.append("".join(current).strip())
            current = []
            i += len(sep)
            continue
        else:
            current.append(text[i])
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _eval_node(node: str, haystack_norm: str) -> bool:
    """
    Évalue récursivement un nœud booléen contre haystack_norm.
    Gère :
      - A AND B NOT C           → A et B présents, C absent
      - (A OR B) AND (C OR D)   → groupes AND au top level
      - A OR B                  → alternatives
      - NOT A                   → A absent (préfixe unaire)
      - "phrase exacte"         → phrase littérale
      - mot                     → mot simple
    Précédence : NOT > AND > OR
    """
    node = node.strip()

    # Retire les parenthèses extérieures inutiles
    while node.startswith("(") and node.endswith(")"):
        depth = 0
        wrapped = True
        for i, c in enumerate(node):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if depth == 0 and i < len(node) - 1:
                wrapped = False
                break
        if wrapped:
            node = node[1:-1].strip()
        else:
            break

    # NOT unaire en préfixe : "NOT expr"
    if node.upper().startswith("NOT "):
        return not _eval_node(node[4:].strip(), haystack_norm)

    # NOT binaire au top level : "expr NOT exclusions"
    # → évalué avant AND pour que "A AND B NOT C" = "(A AND B) NOT C"
    not_parts = _split_top_level(node, " NOT ")
    if len(not_parts) > 1:
        positive = not_parts[0].strip()
        # Toutes les parties après NOT sont des exclusions combinées en OR
        negative = " OR ".join(
            f"({p})" if _split_top_level(p, " OR ") else p
            for p in not_parts[1:]
        )
        pos_ok = _eval_node(positive, haystack_norm) if positive else True
        neg_ok = _eval_node(negative, haystack_norm) if negative else False
        return pos_ok and not neg_ok

    # AND
    and_parts = _split_top_level(node, " AND ")
    if len(and_parts) > 1:
        return all(_eval_node(p, haystack_norm) for p in and_parts)

    # OR
    or_parts = _split_top_level(node, " OR ")
    if len(or_parts) > 1:
        return any(_eval_node(p, haystack_norm) for p in or_parts)

    # Feuille : terme simple ou phrase entre guillemets
    term = node.strip().strip('"').strip("'").strip()
    if not term:
        return False
    return _contains(haystack_norm, term)


def _matches_query(haystack: str, query: str) -> bool:
    """Évalue une query booléenne complète contre le haystack."""
    norm = _normalize(haystack)
    return _eval_node(query.strip(), norm)


def _matches_term(haystack: str, term: str) -> bool:
    """Vérifie si le terme simple est dans le texte."""
    return _contains(_normalize(haystack), term)


# ── Score de pertinence ──────────────────────────────────────────────────────

def _score(rss: RssArticle, keyword: Keyword) -> float:
    """
    Score 0-100 basé sur où le terme est trouvé.
      90  → terme dans le titre
      65  → terme dans le résumé (summary ou début du contenu)
      40  → terme dans le corps de l'article (content intégral)
       0  → pas trouvé
    """
    title_norm   = _normalize(rss.title or "")
    # Résumé : summary explicite ou à défaut les 400 premiers chars du content
    summary_text = rss.summary or (rss.content[:400] if rss.content else "")
    summary_norm = _normalize(summary_text)
    content_norm = _normalize(rss.content or "")

    q = keyword.query.strip() if keyword.query else None

    # 1. Titre → score 90
    if q:
        if _eval_node(q, title_norm):
            return 90.0
    else:
        if _contains(title_norm, keyword.term):
            return 90.0

    # 2. Résumé / début article → score 65
    if q:
        if _eval_node(q, summary_norm):
            return 65.0
    else:
        if _contains(summary_norm, keyword.term):
            return 65.0

    # 3. Corps complet → score 40
    if content_norm:
        if q:
            if _eval_node(q, content_norm):
                return 40.0
        else:
            if _contains(content_norm, keyword.term):
                return 40.0

    return 0.0


def _article_matches_keyword(rss: RssArticle, keyword: Keyword) -> tuple[bool, float]:
    """
    Retourne (match, score).
    Cherche dans : titre (90) → résumé/début (65) → corps complet (40).
    Score minimum pour matcher : MIN_SCORE (défaut 40).
    """
    score = _score(rss, keyword)
    return score >= MIN_SCORE, score


# ── Worker principal ─────────────────────────────────────────────────────────

async def match_rss_batch(db: AsyncSession, batch_size: int = 50) -> dict:
    """
    Traite un batch d'articles RSS non encore matchés.
    Retourne {"processed": N, "matched": M, "articles_created": K, "false_positives_avoided": P}
    """
    stats = {"processed": 0, "matched": 0, "articles_created": 0, "false_positives_avoided": 0}

    # 1. Articles extraits et non encore matchés
    rss_result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.status == "extracted",
            RssArticle.matched_at.is_(None),
        )
        .order_by(RssArticle.collected_at.asc())
        .limit(batch_size)
    )
    rss_articles = rss_result.scalars().all()

    if not rss_articles:
        return stats

    # 2. Charger toutes les (Revue, Keyword) actives
    kw_result = await db.execute(
        select(RevueKeyword, Keyword, Revue)
        .join(Keyword, RevueKeyword.keyword_id == Keyword.id)
        .join(Revue, RevueKeyword.revue_id == Revue.id)
        .where(
            Keyword.is_active == True,
            Revue.is_active == True,
        )
    )
    revue_keywords = kw_result.all()

    if not revue_keywords:
        for rss in rss_articles:
            rss.matched_at = datetime.now(timezone.utc)
            rss.status = "no_match"
            stats["processed"] += 1
        await db.commit()
        return stats

    # 3. Pré-charger les URLs déjà en DB pour déduplication
    rss_urls = [r.url for r in rss_articles]
    existing_result = await db.execute(
        select(Article.url, Article.revue_id)
        .where(Article.url.in_(rss_urls))
    )
    existing_set: set[tuple] = {(row.url, str(row.revue_id)) for row in existing_result}

    # 4. Matching — on groupe tous les keywords matchés par (url, revue_id)
    #    puis on crée UN seul Article par groupe, avec le meilleur keyword en principal
    #    et tous les keywords matchés stockés dans matched_keywords pour le validateur.
    for rss in rss_articles:
        stats["processed"] += 1

        # Collecter tous les matches pour cet article : {(url, revue_id): [(keyword, revue, score)]}
        matches_by_revue: dict[str, list[tuple]] = {}  # revue_id → [(keyword, revue, score)]

        for rk, keyword, revue in revue_keywords:
            dedup_key = (rss.url, str(revue.id))
            if dedup_key in existing_set:
                continue

            matched, score = _article_matches_keyword(rss, keyword)
            if not matched:
                stats["false_positives_avoided"] += 1
                continue

            rid = str(revue.id)
            if rid not in matches_by_revue:
                matches_by_revue[rid] = []
            matches_by_revue[rid].append((keyword, revue, score))

        article_matched = len(matches_by_revue) > 0

        # Créer UN seul Article par revue, avec le meilleur keyword
        for rid, kw_matches in matches_by_revue.items():
            # Trier par score décroissant → keyword principal = meilleur score
            kw_matches.sort(key=lambda x: x[2], reverse=True)
            best_keyword, best_revue, best_score = kw_matches[0]

            # Stocker tous les keywords matchés pour le validateur
            all_matched = [
                {"id": str(kw.id), "term": kw.term, "score": sc}
                for kw, _, sc in kw_matches
            ]

            # Bloquer les URLs Google News non résolues — l'article serait inutilisable
            if "news.google.com" in rss.url:
                continue

            # Bloquer les titres d'erreur / captcha / réseaux sociaux
            _title_low = (rss.title or "").lower().strip()
            _BAD_TITLES = (
                "غير موجودة", "الصفحة غير", "page not found", "404",
                "introuvable", "not found", "un instant", "just a moment",
                "please wait", "attention required", "access denied", "forbidden",
                "instagram", "facebook", "twitter", "tiktok",
            )
            if len(_title_low) < 5 or any(p in _title_low for p in _BAD_TITLES):
                continue

            # Vérification DB avant insert — évite les doublons en race condition
            if (rss.url, rid) in existing_set:
                continue
            dup_check = await db.execute(
                select(Article.id)
                .where(Article.url == rss.url, Article.revue_id == best_revue.id)
                .limit(1)
            )
            if dup_check.scalar_one_or_none():
                existing_set.add((rss.url, rid))
                continue

            source_domain = urlparse(rss.url).netloc.lstrip("www.")
            article = Article(
                revue_id=best_revue.id,
                keyword_id=best_keyword.id,
                url=rss.url,
                source_domain=source_domain,
                collection_method=rss.collection_method or "rss",
                status=ArticleStatus.pending,
                title=rss.title,
                content=rss.content,
                image_url=rss.image_url,
                author=rss.author,
                published_at=rss.published_at,
                summary=rss.summary,
                relevance_score=best_score,
                matched_keywords=all_matched,
                tags=[],
                manually_entered=False,
            )
            db.add(article)
            existing_set.add((rss.url, rid))
            stats["articles_created"] += 1
            stats["matched"] += 1

        rss.matched_at = datetime.now(timezone.utc)
        rss.status = "matched" if article_matched else "no_match"

    await db.commit()
    return stats


# ── Matching rétroactif pour une revue spécifique ───────────────────────────

def _score_text(title: str | None, summary: str | None, keyword: Keyword, content: str | None = None) -> float:
    """Score sur titre → résumé/début → corps complet."""
    title_norm   = _normalize(title or "")
    summary_text = summary or (content[:400] if content else "")
    summary_norm = _normalize(summary_text)
    content_norm = _normalize(content or "")
    q = keyword.query.strip() if keyword.query else None

    if q:
        if _eval_node(q, title_norm):   return 90.0
        if _eval_node(q, summary_norm): return 65.0
        if content_norm and _eval_node(q, content_norm): return 40.0
    else:
        if _contains(title_norm,   keyword.term): return 90.0
        if _contains(summary_norm, keyword.term): return 65.0
        if content_norm and _contains(content_norm, keyword.term): return 40.0
    return 0.0


async def match_rss_for_revue_recent(
    db: AsyncSession,
    revue_id,
    hours: int = 72,
) -> dict:
    """
    Cherche dans TOUTES les sources déjà en base (rss_articles + articles d'autres revues)
    des dernières `hours` heures ceux qui correspondent aux keywords de cette revue.
    Crée les Article pending manquants. Source-agnostique (SerpAPI, RSS, GDELT, etc.).
    """
    from datetime import timedelta
    from urllib.parse import urlparse as _urlparse

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    stats = {"processed": 0, "matched": 0, "articles_created": 0}

    # Keywords actifs de cette revue
    kw_result = await db.execute(
        select(RevueKeyword, Keyword, Revue)
        .join(Keyword, RevueKeyword.keyword_id == Keyword.id)
        .join(Revue, RevueKeyword.revue_id == Revue.id)
        .where(
            RevueKeyword.revue_id == revue_id,
            Keyword.is_active == True,
            Revue.is_active == True,
        )
    )
    revue_keywords = kw_result.all()
    if not revue_keywords:
        return stats

    # URLs déjà présentes pour cette revue (déduplication)
    existing_result = await db.execute(
        select(Article.url).where(Article.revue_id == revue_id)
    )
    existing_urls: set[str] = {row.url for row in existing_result}

    # ── Source 1 : rss_articles (enrichis) ──────────────────────────────────
    rss_result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.collected_at >= cutoff,
            RssArticle.title.isnot(None),
        )
        .order_by(RssArticle.collected_at.desc())
    )
    rss_candidates = rss_result.scalars().all()

    for rss in rss_candidates:
        if rss.url in existing_urls:
            stats["processed"] += 1
            continue
        stats["processed"] += 1
        for rk, keyword, revue in revue_keywords:
            score = _score_text(rss.title, rss.summary, keyword, rss.content)
            if score < MIN_SCORE:
                continue
            # Vérification DB avant insert — évite les doublons en race condition
            dup = await db.execute(
                select(Article.id)
                .where(Article.url == rss.url, Article.revue_id == revue.id)
                .limit(1)
            )
            if dup.scalar_one_or_none():
                existing_urls.add(rss.url)
                break
            source_domain = _urlparse(rss.url).netloc.lstrip("www.")
            db.add(Article(
                revue_id=revue.id, keyword_id=keyword.id,
                url=rss.url, source_domain=source_domain,
                collection_method="rss",
                status=ArticleStatus.pending,
                title=rss.title, content=rss.content,
                image_url=rss.image_url, author=rss.author,
                published_at=rss.published_at, summary=rss.summary,
                relevance_score=score, tags=[], manually_entered=False,
            ))
            existing_urls.add(rss.url)
            stats["articles_created"] += 1
            stats["matched"] += 1
            break

    # ── Source 2 : articles existants (autres revues, SerpAPI, GDELT…) ──────
    art_result = await db.execute(
        select(Article)
        .where(
            Article.revue_id != revue_id,
            Article.created_at >= cutoff,
            Article.title.isnot(None),
        )
        .order_by(Article.created_at.desc())
    )
    art_candidates = art_result.scalars().all()

    for art in art_candidates:
        if art.url in existing_urls:
            stats["processed"] += 1
            continue
        stats["processed"] += 1
        for rk, keyword, revue in revue_keywords:
            score = _score_text(art.title, art.summary, keyword, art.content)
            if score < MIN_SCORE:
                continue
            # Vérification DB avant insert — évite les doublons en race condition
            dup = await db.execute(
                select(Article.id)
                .where(Article.url == art.url, Article.revue_id == revue.id)
                .limit(1)
            )
            if dup.scalar_one_or_none():
                existing_urls.add(art.url)
                break
            source_domain = _urlparse(art.url).netloc.lstrip("www.")
            db.add(Article(
                revue_id=revue.id, keyword_id=keyword.id,
                url=art.url, source_domain=source_domain,
                collection_method=art.collection_method,
                status=ArticleStatus.pending,
                title=art.title, content=art.content,
                image_url=art.image_url, author=art.author,
                published_at=art.published_at, summary=art.summary,
                relevance_score=score, tags=[], manually_entered=False,
            ))
            existing_urls.add(art.url)
            stats["articles_created"] += 1
            stats["matched"] += 1
            break

    await db.commit()
    return stats


async def preview_match_for_revue(
    db: AsyncSession,
    revue_id,
    days: int = 7,
) -> list[dict]:
    """
    Dry-run : retourne les articles RSS des X derniers jours qui matcheraient
    les keywords de cette revue, SANS créer d'Article en base.
    Utilisé pour valider la config mots-clés lors de l'onboarding.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Keywords actifs de cette revue
    kw_result = await db.execute(
        select(RevueKeyword, Keyword)
        .join(Keyword, RevueKeyword.keyword_id == Keyword.id)
        .where(
            RevueKeyword.revue_id == revue_id,
            Keyword.is_active == True,
        )
    )
    revue_keywords = kw_result.all()
    if not revue_keywords:
        return []

    # Articles RSS des X derniers jours avec titre
    rss_result = await db.execute(
        select(RssArticle)
        .where(
            RssArticle.collected_at >= cutoff,
            RssArticle.title.isnot(None),
        )
        .order_by(RssArticle.collected_at.desc())
        .limit(3000)
    )
    candidates = rss_result.scalars().all()

    seen_urls: set[str] = set()
    matches: list[dict] = []

    for rss in candidates:
        if rss.url in seen_urls:
            continue
        for rk, keyword in revue_keywords:
            score = _score_text(rss.title, rss.summary, keyword, rss.content)
            if score >= MIN_SCORE:
                seen_urls.add(rss.url)
                matches.append({
                    "url": rss.url,
                    "title": rss.title,
                    "source_domain": urlparse(rss.url).netloc.lstrip("www."),
                    "published_at": rss.published_at.isoformat() if rss.published_at else None,
                    "collected_at": rss.collected_at.isoformat() if rss.collected_at else None,
                    "keyword_term": keyword.term,
                    "score": score,
                    "image_url": rss.image_url,
                    "summary": rss.summary,
                })
                break

    # Trier par date décroissante
    matches.sort(key=lambda x: x.get("collected_at") or "", reverse=True)
    return matches


# ── Réinitialisation des articles mal matchés ────────────────────────────────

async def reset_bad_matches(db: AsyncSession) -> dict:
    """
    Supprime les articles pending créés avec un score de 40 (content-only, faux positifs)
    et réinitialise matched_at pour que le worker les retraite avec le nouveau parser.
    """
    from sqlalchemy import delete, update, text

    # Supprimer les articles pending avec score 40 (créés par l'ancien parser)
    del_result = await db.execute(
        delete(Article)
        .where(
            Article.status == ArticleStatus.pending,
            Article.relevance_score == 40.0,
            Article.manually_entered == False,
        )
        .returning(Article.id)
    )
    deleted = len(del_result.fetchall())

    # Réinitialiser matched_at sur tous les rss_articles pour les re-matcher
    await db.execute(
        update(RssArticle).values(matched_at=None)
    )

    await db.commit()
    return {"deleted_bad_articles": deleted, "rss_reset": True}
