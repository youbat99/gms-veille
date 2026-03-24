"""
API pour le clustering d'articles similaires.
Un cluster = un événement couvert par plusieurs sources.
"""
import uuid
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin_plus
from app.models.article import Article, ArticleStatus
from app.models.article_cluster import ArticleCluster, ArticleClusterMember
from app.models.client import Account, AccountRole
from app.models.revue import Revue
from app.services.clustering_service import cluster_articles_for_revue

router = APIRouter(prefix="/clusters", tags=["clusters"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class ArticleInClusterOut(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    url: str
    source_domain: Optional[str]
    published_at: Optional[datetime]
    similarity_score: float
    is_source: bool
    status: str
    relevance_score: Optional[float]
    tonality: Optional[str]
    keyword_term: Optional[str]
    image_url: Optional[str]

    class Config:
        from_attributes = True


class ClusterOut(BaseModel):
    id: uuid.UUID
    title: str
    event_date: date
    article_count: int
    keyword_term: Optional[str]
    pending_count: int
    articles: list[ArticleInClusterOut]
    created_at: datetime

    class Config:
        from_attributes = True


class ValidateClusterBody(BaseModel):
    action: str          # "approve" | "reject"
    apply_to: str = "all"  # "all" | "source_only"


class ComputeResult(BaseModel):
    clusters_created: int
    articles_clustered: int
    articles_total: int


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _assert_revue_access(revue_id: uuid.UUID, user: Account, db: AsyncSession) -> Revue:
    result = await db.execute(select(Revue).where(Revue.id == revue_id))
    revue = result.scalar_one_or_none()
    if not revue:
        raise HTTPException(404, "Revue introuvable")
    if user.role not in (AccountRole.super_admin, AccountRole.admin):
        from app.models.client import UserAccount
        ua = await db.execute(
            select(UserAccount).where(
                and_(UserAccount.account_id == user.id, UserAccount.revue_id == revue_id)
            )
        )
        if not ua.scalar_one_or_none():
            raise HTTPException(403, "Accès refusé")
    return revue


async def _load_cluster_with_articles(cluster_id: uuid.UUID, db: AsyncSession) -> ClusterOut:
    """Charge un cluster avec ses articles membres."""
    result = await db.execute(
        select(ArticleCluster)
        .where(ArticleCluster.id == cluster_id)
        .options(selectinload(ArticleCluster.members).selectinload(ArticleClusterMember.article))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(404, "Cluster introuvable")

    articles_out = []
    pending_count = 0

    for m in sorted(cluster.members, key=lambda x: (-x.is_source, -x.similarity_score)):
        a = m.article
        if a.status == ArticleStatus.pending:
            pending_count += 1

        # Récupérer le keyword_term depuis le keyword lié
        kw_term = None
        if a.keyword_id:
            from app.models.revue import Keyword
            kw_res = await db.execute(select(Keyword.term).where(Keyword.id == a.keyword_id))
            kw_term = kw_res.scalar_one_or_none()

        articles_out.append(ArticleInClusterOut(
            id=a.id,
            title=a.title,
            url=a.url,
            source_domain=a.source_domain,
            published_at=a.published_at,
            similarity_score=m.similarity_score,
            is_source=m.is_source,
            status=a.status.value,
            relevance_score=a.relevance_score,
            tonality=a.tonality.value if a.tonality else None,
            keyword_term=kw_term,
            image_url=a.image_url,
        ))

    return ClusterOut(
        id=cluster.id,
        title=cluster.title,
        event_date=cluster.event_date,
        article_count=cluster.article_count,
        keyword_term=cluster.keyword_term,
        pending_count=pending_count,
        articles=articles_out,
        created_at=cluster.created_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/revue/{revue_id}", response_model=list[ClusterOut])
async def list_clusters_for_revue(
    revue_id: uuid.UUID,
    target_date: Optional[date] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(get_current_user),
):
    """Liste les clusters d'une revue pour une date (défaut: aujourd'hui)."""
    await _assert_revue_access(revue_id, user, db)

    if target_date is None:
        target_date = date.today()

    result = await db.execute(
        select(ArticleCluster)
        .where(
            and_(
                ArticleCluster.revue_id == revue_id,
                ArticleCluster.event_date == target_date,
            )
        )
        .order_by(ArticleCluster.article_count.desc())
    )
    clusters = result.scalars().all()

    out = []
    for c in clusters:
        try:
            out.append(await _load_cluster_with_articles(c.id, db))
        except Exception:
            pass
    return out


@router.get("/{cluster_id}", response_model=ClusterOut)
async def get_cluster(
    cluster_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(get_current_user),
):
    """Détail d'un cluster avec tous ses articles."""
    cluster_out = await _load_cluster_with_articles(cluster_id, db)
    await _assert_revue_access(cluster_out.id, user, db)
    return cluster_out


@router.post("/revue/{revue_id}/compute", response_model=ComputeResult)
async def compute_clusters(
    revue_id: uuid.UUID,
    target_date: Optional[date] = Query(None, alias="date"),
    threshold: float = Query(0.35, ge=0.1, le=0.9),
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(get_current_user),
):
    """Déclenche le clustering manuellement pour une revue/date."""
    await _assert_revue_access(revue_id, user, db)

    if target_date is None:
        target_date = date.today()

    stats = await cluster_articles_for_revue(db, revue_id, target_date, threshold=threshold)
    return ComputeResult(**stats)


@router.patch("/{cluster_id}/validate")
async def validate_cluster(
    cluster_id: uuid.UUID,
    body: ValidateClusterBody,
    db: AsyncSession = Depends(get_db),
    user: Account = Depends(get_current_user),
):
    """
    Valide ou rejette tous les articles d'un cluster en une action.
    apply_to: "all" (tous) ou "source_only" (uniquement l'article source).
    """
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "action doit être 'approve' ou 'reject'")

    # Charger le cluster + membres
    result = await db.execute(
        select(ArticleCluster)
        .where(ArticleCluster.id == cluster_id)
        .options(selectinload(ArticleCluster.members))
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(404, "Cluster introuvable")

    await _assert_revue_access(cluster.revue_id, user, db)

    # Déterminer les articles à traiter
    if body.apply_to == "source_only":
        target_member_ids = [m.article_id for m in cluster.members if m.is_source]
    else:
        target_member_ids = [m.article_id for m in cluster.members]

    if not target_member_ids:
        raise HTTPException(400, "Aucun article à valider")

    new_status = ArticleStatus.approved if body.action == "approve" else ArticleStatus.rejected

    await db.execute(
        update(Article)
        .where(Article.id.in_(target_member_ids))
        .values(
            status=new_status,
            validated_at=datetime.utcnow(),
            validated_by=user.id,
        )
    )
    await db.commit()

    return {
        "ok": True,
        "action": body.action,
        "articles_updated": len(target_member_ids),
        "cluster_id": str(cluster_id),
    }
