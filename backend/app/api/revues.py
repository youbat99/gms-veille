"""
Gestion des revues de presse et de leurs mots-clés.
Fréquence / paramètres SerpAPI gérés par SchedulerSlot, pas par keyword.
"""
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin_plus
from app.models.client import Account, AccountRole, Client, UserAccount
from app.models.revue import Revue, Keyword, RevueKeyword, KeywordType
from app.models.article import Article, ArticleModificationLog
from app.models.article_read import ArticleRead

router = APIRouter(prefix="/revues", tags=["revues"])


# ── Schemas ─────────────────────────────────────────────────────────────────
class RevueCreate(BaseModel):
    name: str
    description: str | None = None
    client_id: uuid.UUID


class RevueOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class RevueUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


class KeywordCreate(BaseModel):
    term: str
    query: str | None = None
    query_json: dict | None = None
    language: str = "fr"
    type: KeywordType = KeywordType.transversal


class KeywordUpdate(BaseModel):
    query: str | None = None
    query_json: dict | None = None
    is_active: bool | None = None


class KeywordOut(BaseModel):
    id: uuid.UUID
    term: str
    query: str | None
    query_json: dict | None
    language: str
    type: KeywordType
    is_active: bool

    class Config:
        from_attributes = True


class KeywordSerpConfig(BaseModel):
    """Config SerpAPI propre au keyword dans cette revue (nullable = utilise le slot global)."""
    tbs:         str | None = None
    gl:          str | None = None
    language:    str | None = None
    num_results: int | None = None
    sort_by:     str | None = None
    safe_search: bool | None = None


class KeywordWithSerpOut(KeywordOut):
    """KeywordOut enrichi de la config SerpAPI du RevueKeyword."""
    serp: KeywordSerpConfig


def _query_json_to_string(qj: dict) -> str:
    """Génère la requête booléenne string depuis la structure JSON du query builder."""
    include: list[list[str]] = qj.get("include", [])
    exclude: list[str] = qj.get("exclude", [])

    def fmt(t: str) -> str:
        return f'"{t}"' if (" " in t or any(c in t for c in "()\"'")) else t

    groups = [g for g in include if g]
    if not groups:
        return ""

    inc_parts = []
    for group in groups:
        if len(group) == 1:
            inc_parts.append(fmt(group[0]))
        else:
            inc_parts.append("(" + " OR ".join(fmt(t) for t in group) + ")")

    result = " AND ".join(inc_parts)

    if exclude:
        if len(exclude) == 1:
            result += f" NOT {fmt(exclude[0])}"
        else:
            result += " NOT (" + " OR ".join(fmt(t) for t in exclude) + ")"

    return result


class RevueDetailOut(RevueOut):
    keywords: list[KeywordOut] = []


# ── Helpers ─────────────────────────────────────────────────────────────────
async def _assert_revue_access(revue: Revue, user: Account):
    if user.role == AccountRole.admin and revue.client_id != user.client_id:
        raise HTTPException(403, "Accès refusé")


# ── Endpoints ────────────────────────────────────────────────────────────────
@router.get("/mine", response_model=list[RevueOut])
async def my_revues(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == AccountRole.super_admin:
        result = await db.execute(select(Revue).where(Revue.is_active == True).order_by(Revue.name))
    else:
        result = await db.execute(
            select(Revue)
            .where(Revue.client_id == current_user.client_id, Revue.is_active == True)
            .order_by(Revue.name)
        )
    return result.scalars().all()


@router.get("/", response_model=list[RevueOut])
async def list_revues(
    client_id: uuid.UUID | None = None,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == AccountRole.super_admin:
        q = select(Revue)
        if client_id:
            q = q.where(Revue.client_id == client_id)
        result = await db.execute(q.order_by(Revue.created_at.desc()))
    else:
        q = select(Revue).where(Revue.client_id == current_user.client_id)
        result = await db.execute(q.order_by(Revue.created_at.desc()))
    return result.scalars().all()


@router.post("/", response_model=RevueOut, status_code=201)
async def create_revue(
    body: RevueCreate,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    client_id = current_user.client_id if current_user.role == AccountRole.admin else body.client_id
    client = await db.get(Client, client_id)
    if not client:
        raise HTTPException(404, "Client introuvable")

    revue = Revue(client_id=client_id, name=body.name, description=body.description)
    db.add(revue)
    await db.commit()
    await db.refresh(revue)
    return revue


@router.get("/{revue_id}", response_model=RevueDetailOut)
async def get_revue(
    revue_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Revue)
        .where(Revue.id == revue_id)
        .options(selectinload(Revue.revue_keywords).selectinload(RevueKeyword.keyword))
    )
    revue = result.scalar_one_or_none()
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    keywords = [
        KeywordOut(
            id=rk.keyword.id,
            term=rk.keyword.term,
            query=rk.keyword.query,
            query_json=rk.keyword.query_json,
            language=rk.keyword.language,
            type=rk.keyword.type,
            is_active=rk.keyword.is_active,
        )
        for rk in revue.revue_keywords
    ]

    return RevueDetailOut(
        id=revue.id,
        client_id=revue.client_id,
        name=revue.name,
        description=revue.description,
        is_active=revue.is_active,
        created_at=revue.created_at,
        keywords=keywords,
    )


@router.delete("/{revue_id}", status_code=204)
async def delete_revue(
    revue_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Supprime une revue et toutes ses données (articles, keywords, slots, accès utilisateurs)."""
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    # Suppression explicite dans l'ordre (évite FK violations)
    # Sous-requête : IDs des articles de cette revue
    art_ids_subq = select(Article.id).where(Article.revue_id == revue_id)
    # 1. Logs de modification des articles
    await db.execute(delete(ArticleModificationLog).where(ArticleModificationLog.article_id.in_(art_ids_subq)))
    # 2. Lectures des articles
    await db.execute(delete(ArticleRead).where(ArticleRead.article_id.in_(art_ids_subq)))
    # 3. Articles
    await db.execute(delete(Article).where(Article.revue_id == revue_id))
    # 4. Accès utilisateurs liés à cette revue
    await db.execute(delete(UserAccount).where(UserAccount.revue_id == revue_id))
    # 5. Mots-clés de la revue
    await db.execute(delete(RevueKeyword).where(RevueKeyword.revue_id == revue_id))
    # 6. Supprimer la revue elle-même
    await db.execute(delete(Revue).where(Revue.id == revue_id))
    await db.commit()


@router.patch("/{revue_id}", response_model=RevueOut)
async def update_revue(
    revue_id: uuid.UUID,
    body: RevueUpdate,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    if body.name is not None:
        revue.name = body.name
    if body.description is not None:
        revue.description = body.description
    if body.is_active is not None:
        revue.is_active = body.is_active

    await db.commit()
    await db.refresh(revue)
    return revue


# ── Keyword management ───────────────────────────────────────────────────────
@router.get("/{revue_id}/keywords", response_model=list[KeywordWithSerpOut])
async def list_keywords(
    revue_id: uuid.UUID,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retourne les mots-clés d'une revue avec leur config SerpAPI propre."""
    result = await db.execute(
        select(Revue)
        .where(Revue.id == revue_id)
        .options(selectinload(Revue.revue_keywords).selectinload(RevueKeyword.keyword))
    )
    revue = result.scalar_one_or_none()
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    return [
        KeywordWithSerpOut(
            id=rk.keyword.id,
            term=rk.keyword.term,
            query=rk.keyword.query,
            query_json=rk.keyword.query_json,
            language=rk.keyword.language,
            type=rk.keyword.type,
            is_active=rk.keyword.is_active,
            serp=KeywordSerpConfig(
                tbs=rk.tbs,
                gl=rk.gl,
                language=rk.language,
                num_results=rk.num_results,
                sort_by=rk.sort_by,
                safe_search=rk.safe_search,
            ),
        )
        for rk in revue.revue_keywords
    ]


@router.patch("/{revue_id}/keywords/{keyword_id}/serp-config", response_model=KeywordSerpConfig)
async def update_keyword_serp_config(
    revue_id: uuid.UUID,
    keyword_id: uuid.UUID,
    body: KeywordSerpConfig,
    _: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour la config SerpAPI propre à ce keyword dans cette revue. Mettre null pour revenir au défaut du slot."""
    rk = await db.get(RevueKeyword, (revue_id, keyword_id))
    if not rk:
        raise HTTPException(404, "Keyword introuvable dans cette revue")

    rk.tbs         = body.tbs
    rk.gl          = body.gl
    rk.language    = body.language
    rk.num_results = body.num_results
    rk.sort_by     = body.sort_by
    rk.safe_search = body.safe_search

    await db.commit()
    return KeywordSerpConfig(
        tbs=rk.tbs, gl=rk.gl, language=rk.language,
        num_results=rk.num_results, sort_by=rk.sort_by, safe_search=rk.safe_search,
    )


@router.post("/{revue_id}/keywords", response_model=KeywordOut, status_code=201)
async def add_keyword(
    revue_id: uuid.UUID,
    body: KeywordCreate,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    existing_kw = None
    if body.type == KeywordType.transversal:
        existing_kw = await db.scalar(
            select(Keyword).where(
                Keyword.term == body.term,
                Keyword.type == KeywordType.transversal,
            )
        )

    # Si query_json fourni, générer le query string depuis la structure visuelle
    resolved_query = body.query
    if body.query_json is not None:
        resolved_query = _query_json_to_string(body.query_json) or body.query

    if not existing_kw:
        existing_kw = Keyword(
            term=body.term, query=resolved_query,
            query_json=body.query_json,
            language=body.language, type=body.type,
        )
        db.add(existing_kw)
        await db.flush()
    elif resolved_query is not None:
        existing_kw.query = resolved_query
        existing_kw.query_json = body.query_json

    already = await db.scalar(
        select(RevueKeyword).where(
            RevueKeyword.revue_id == revue_id,
            RevueKeyword.keyword_id == existing_kw.id,
        )
    )
    if already:
        raise HTTPException(400, "Ce mot-clé est déjà ajouté à cette revue")

    rk = RevueKeyword(revue_id=revue_id, keyword_id=existing_kw.id)
    db.add(rk)
    await db.commit()
    await db.refresh(existing_kw)

    return KeywordOut(
        id=existing_kw.id, term=existing_kw.term, query=existing_kw.query,
        query_json=existing_kw.query_json,
        language=existing_kw.language, type=existing_kw.type, is_active=existing_kw.is_active,
    )


@router.patch("/{revue_id}/keywords/{keyword_id}", response_model=KeywordOut)
async def update_keyword(
    revue_id: uuid.UUID,
    keyword_id: uuid.UUID,
    body: KeywordUpdate,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    kw = await db.get(Keyword, keyword_id)
    if not kw:
        raise HTTPException(404, "Mot-clé introuvable")

    if body.query_json is not None:
        kw.query_json = body.query_json
        kw.query = _query_json_to_string(body.query_json) or kw.query
    elif body.query is not None:
        kw.query = body.query or None
        kw.query_json = None  # reset le JSON si on édite en mode code
    if body.is_active is not None:
        kw.is_active = body.is_active

    await db.commit()
    await db.refresh(kw)

    return KeywordOut(
        id=kw.id, term=kw.term, query=kw.query,
        query_json=kw.query_json,
        language=kw.language, type=kw.type, is_active=kw.is_active,
    )


@router.post("/{revue_id}/keywords/{keyword_id}/copy", response_model=KeywordOut, status_code=201)
async def copy_keyword_to_revue(
    revue_id: uuid.UUID,
    keyword_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    kw = await db.get(Keyword, keyword_id)
    if not kw:
        raise HTTPException(404, "Mot-clé introuvable")

    already = await db.scalar(
        select(RevueKeyword).where(
            RevueKeyword.revue_id == revue_id,
            RevueKeyword.keyword_id == keyword_id,
        )
    )
    if already:
        raise HTTPException(400, "Ce mot-clé est déjà dans cette revue")

    rk = RevueKeyword(revue_id=revue_id, keyword_id=keyword_id)
    db.add(rk)
    await db.commit()

    return KeywordOut(
        id=kw.id, term=kw.term, query=kw.query,
        query_json=kw.query_json,
        language=kw.language, type=kw.type, is_active=kw.is_active,
    )


@router.post("/{revue_id}/match-recent")
async def match_recent_rss_for_revue(
    revue_id: uuid.UUID,
    hours: int = 24,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """
    Cherche dans les articles RSS déjà crawlés (dernières `hours` heures)
    ceux qui correspondent aux keywords de cette revue, et crée les Article
    pending manquants. Ne fait aucun appel externe (SerpAPI, etc.).
    """
    from app.services.rss_matching_service import match_rss_for_revue_recent
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)
    result = await match_rss_for_revue_recent(db=db, revue_id=revue_id, hours=hours)
    return result


@router.get("/keywords/all")
async def list_all_keywords(
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Keyword, RevueKeyword, Revue)
        .join(RevueKeyword, RevueKeyword.keyword_id == Keyword.id)
        .join(Revue, Revue.id == RevueKeyword.revue_id)
        .order_by(Keyword.term)
    )
    seen = {}
    for kw, rk, revue in result.all():
        if str(kw.id) not in seen:
            seen[str(kw.id)] = {
                "id": str(kw.id), "term": kw.term,
                "language": kw.language, "type": kw.type.value,
                "is_active": kw.is_active, "revues": [],
            }
        seen[str(kw.id)]["revues"].append({"id": str(revue.id), "name": revue.name})
    return list(seen.values())


@router.post("/{revue_id}/preview-match")
async def preview_match(
    revue_id: uuid.UUID,
    days: int = 7,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    """
    Dry-run sur les X derniers jours : retourne les articles qui matcheraient
    les keywords de cette revue, SANS rien créer en base.
    Utilisé pour valider la configuration mots-clés lors de l'onboarding.
    """
    from app.services.rss_matching_service import preview_match_for_revue
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)
    matches = await preview_match_for_revue(db=db, revue_id=revue_id, days=days)
    return {"matches": matches, "count": len(matches), "days": days}


@router.delete("/{revue_id}/keywords/{keyword_id}", status_code=204)
async def remove_keyword(
    revue_id: uuid.UUID,
    keyword_id: uuid.UUID,
    current_user: Account = Depends(require_admin_plus),
    db: AsyncSession = Depends(get_db),
):
    revue = await db.get(Revue, revue_id)
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    await _assert_revue_access(revue, current_user)

    rk = await db.scalar(
        select(RevueKeyword).where(
            RevueKeyword.revue_id == revue_id,
            RevueKeyword.keyword_id == keyword_id,
        )
    )
    if not rk:
        raise HTTPException(404, "Mot-clé non trouvé dans cette revue")

    await db.delete(rk)
    await db.commit()
