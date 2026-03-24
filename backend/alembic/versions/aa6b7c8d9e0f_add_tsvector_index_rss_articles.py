"""add tsvector GIN index on rss_articles for full-text search

Revision ID: aa6b7c8d9e0f
Revises: z5a6b7c8d9e0
Create Date: 2026-03-23

Remplace les ILIKE %term% (table-scan) par un index GIN sur tsvector.
Utilise le dictionnaire 'simple' (tokenisation sans stemming) pour supporter
l'arabe, le français et l'anglais dans la même colonne.
Gain de performance estimé : 50-200x sur 100 000+ articles.
"""
from alembic import op

revision = 'aa6b7c8d9e0f'
down_revision = 'z5a6b7c8d9e0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index GIN sur la concaténation title + summary
    # 'simple' = pas de stemming → fonctionne pour AR/FR/EN
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_rss_articles_search_fts
        ON rss_articles
        USING gin(
            to_tsvector(
                'simple',
                coalesce(title, '') || ' ' || coalesce(summary, '')
            )
        )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rss_articles_search_fts")
