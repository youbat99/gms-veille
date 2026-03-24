"""
Service de clustering d'articles similaires.

Algorithme : Jaccard sur titres normalisés (AR/FR/EN) + Union-Find.
Un cluster = un événement réel couvert par plusieurs sources.

Seuil par défaut : 0.35 (calibré pour titres courts multilingues).
"""
import re
import uuid
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, func

from app.models.article import Article, ArticleStatus
from app.models.article_cluster import ArticleCluster, ArticleClusterMember
from app.models.revue import Revue

logger = logging.getLogger(__name__)

# ── Stopwords multilingues ───────────────────────────────────────────────────

STOPWORDS_FR = {
    "le", "la", "les", "de", "du", "des", "au", "aux", "en", "et", "un", "une",
    "à", "par", "pour", "sur", "dans", "avec", "est", "sont", "se", "qui", "que",
    "qu", "ce", "il", "elle", "ils", "elles", "nous", "vous", "on", "its", "their",
    "sa", "son", "ses", "mon", "ton", "ma", "ta", "mes", "tes", "nos", "vos",
    "cette", "cet", "ces", "plus", "mais", "ou", "où", "ne", "pas", "très",
    "lors", "dont", "après", "avant", "entre", "vers", "sous", "sans", "aussi",
}

STOPWORDS_AR = {
    "في", "من", "إلى", "على", "و", "أن", "هذا", "التي", "الذي", "هو", "هي",
    "كان", "كانت", "مع", "عن", "بعد", "قبل", "حتى", "كما", "لكن", "أو",
    "أيضا", "فى", "إن", "ما", "لا", "قد", "كل", "ذلك", "هذه", "تلك",
    "بين", "خلال", "منذ", "حول", "بسبب", "رغم", "وفق", "وفقا", "بشأن",
    "ال", "الا", "بال", "فال", "والا", "فالا",
}

STOPWORDS_EN = {
    "the", "a", "an", "of", "in", "to", "and", "is", "are", "was", "were",
    "for", "on", "at", "by", "from", "with", "it", "its", "this", "that",
    "be", "been", "has", "have", "had", "do", "does", "did", "will", "would",
    "as", "or", "but", "not", "he", "she", "they", "we", "you", "i",
}

STOPWORDS_ALL = STOPWORDS_FR | STOPWORDS_AR | STOPWORDS_EN


def _normalize_title(title: str) -> set[str]:
    """
    Tokenise + lowercase + supprime stopwords + supprime tokens < 3 chars.
    Gère AR/FR/EN dans le même titre (ex: revue bilingue).
    """
    if not title:
        return set()
    # Lowercase
    t = title.lower()
    # Supprimer la ponctuation (garder lettres arabes, latines, chiffres)
    t = re.sub(r"[^\w\s\u0600-\u06FF]", " ", t)
    # Tokeniser
    tokens = t.split()
    # Filtrer stopwords et tokens courts
    return {tok for tok in tokens if tok not in STOPWORDS_ALL and len(tok) >= 3}


def _jaccard(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Coefficient de Jaccard entre deux ensembles de tokens."""
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


# ── Union-Find ───────────────────────────────────────────────────────────────

class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1

    def groups(self) -> dict[int, list[int]]:
        """Retourne les groupes : {root_idx: [idx1, idx2, ...]}"""
        from collections import defaultdict
        g: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            g[self.find(i)].append(i)
        return dict(g)


# ── Clustering principal ─────────────────────────────────────────────────────

async def cluster_articles_for_revue(
    db: AsyncSession,
    revue_id: str | uuid.UUID,
    target_date: date,
    threshold: float = 0.35,
) -> dict:
    """
    Calcule les clusters pour une revue sur une date donnée.

    Étapes :
    1. Récupère les articles pending/approved/modified de la revue pour cette date
    2. Calcule la similarité Jaccard pairwise sur les titres
    3. Forme les clusters via Union-Find
    4. Supprime les anciens clusters de ce jour, insère les nouveaux
    5. Retourne un résumé

    Un cluster doit avoir au moins 2 articles (les solitaires sont ignorés).
    """
    revue_uuid = uuid.UUID(str(revue_id))

    # 1. Récupérer les articles du jour
    result = await db.execute(
        select(Article)
        .where(
            and_(
                Article.revue_id == revue_uuid,
                Article.status.in_([
                    ArticleStatus.pending,
                    ArticleStatus.approved,
                    ArticleStatus.modified,
                ]),
                Article.title.isnot(None),
                # Articles publiés ou collectés ce jour
                func.coalesce(
                    func.date(Article.published_at),
                    func.date(Article.created_at)
                ) == target_date,
            )
        )
        .order_by(Article.created_at.asc())
    )
    articles = result.scalars().all()

    if len(articles) < 2:
        return {"clusters_created": 0, "articles_clustered": 0, "articles_total": len(articles)}

    # 2. Normaliser les titres
    tokens_list = [_normalize_title(a.title or "") for a in articles]
    n = len(articles)

    # 3. Similarité pairwise + Union-Find
    uf = UnionFind(n)
    # Matrice de similarité (on ne stocke que les paires similaires)
    sim_pairs: dict[tuple[int, int], float] = {}

    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard(tokens_list[i], tokens_list[j])
            if sim >= threshold:
                uf.union(i, j)
                sim_pairs[(i, j)] = sim

    # 4. Extraire les groupes avec ≥ 2 membres
    groups = uf.groups()
    real_clusters = {root: members for root, members in groups.items() if len(members) >= 2}

    if not real_clusters:
        return {"clusters_created": 0, "articles_clustered": 0, "articles_total": n}

    # 5. Supprimer anciens clusters de ce jour pour cette revue
    # (clustering idempotent — on recalcule proprement)
    old_clusters_result = await db.execute(
        select(ArticleCluster.id).where(
            and_(
                ArticleCluster.revue_id == revue_uuid,
                ArticleCluster.event_date == target_date,
            )
        )
    )
    old_ids = old_clusters_result.scalars().all()
    if old_ids:
        await db.execute(
            delete(ArticleCluster).where(ArticleCluster.id.in_(old_ids))
        )

    # 6. Créer les nouveaux clusters
    clusters_created = 0
    articles_clustered = 0

    for root, member_indices in real_clusters.items():
        member_articles = [articles[i] for i in member_indices]

        # Trouver l'article "source" : celui avec la date de publication la plus ancienne
        # (ou le premier collecté si published_at est NULL)
        def sort_key(a: Article):
            if a.published_at:
                return a.published_at
            return a.created_at or datetime.min

        sorted_members = sorted(member_articles, key=sort_key)
        source_article = sorted_members[0]

        # Titre du cluster = titre de la source
        cluster_title = source_article.title or "Sans titre"

        # Mot-clé dominant (le plus fréquent dans le cluster)
        kw_counts: dict[str, int] = {}
        for a in member_articles:
            kw = getattr(a, "keyword_term", None)
            if kw:
                kw_counts[kw] = kw_counts.get(kw, 0) + 1
        dominant_kw = max(kw_counts, key=lambda k: kw_counts[k]) if kw_counts else None

        # Créer le cluster
        cluster = ArticleCluster(
            revue_id=revue_uuid,
            title=cluster_title[:1024],
            event_date=target_date,
            article_count=len(member_articles),
            keyword_term=dominant_kw,
        )
        db.add(cluster)
        await db.flush()  # pour obtenir cluster.id

        # Créer les membres
        for idx in member_indices:
            a = articles[idx]
            is_src = (a.id == source_article.id)

            # Score de similarité vs source
            if is_src:
                score = 1.0
            else:
                src_idx = articles.index(source_article)
                i_min, i_max = min(idx, src_idx), max(idx, src_idx)
                score = sim_pairs.get((i_min, i_max), threshold)

            member = ArticleClusterMember(
                cluster_id=cluster.id,
                article_id=a.id,
                similarity_score=round(score, 3),
                is_source=is_src,
            )
            db.add(member)

        clusters_created += 1
        articles_clustered += len(member_articles)

    await db.commit()

    logger.info(
        f"[clustering] revue={revue_uuid} date={target_date} → "
        f"{clusters_created} clusters, {articles_clustered}/{n} articles"
    )

    return {
        "clusters_created": clusters_created,
        "articles_clustered": articles_clustered,
        "articles_total": n,
    }


async def cluster_all_active_revues(db: AsyncSession) -> dict:
    """
    Lance le clustering pour toutes les revues actives sur aujourd'hui et hier.
    Appelé par le worker toutes les 30min.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)

    result = await db.execute(
        select(Revue.id).where(Revue.is_active == True)
    )
    revue_ids = result.scalars().all()

    total_clusters = 0
    total_articles = 0

    for revue_id in revue_ids:
        for d in [today, yesterday]:
            try:
                stats = await cluster_articles_for_revue(db, revue_id, d)
                total_clusters += stats["clusters_created"]
                total_articles += stats["articles_clustered"]
            except Exception as e:
                logger.error(f"[clustering] erreur revue={revue_id} date={d}: {e}")

    return {"total_clusters": total_clusters, "total_articles": total_articles, "revues": len(revue_ids)}
