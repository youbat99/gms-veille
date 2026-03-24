"""add performance indexes on articles and rss_articles

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
Create Date: 2026-03-23
"""
from alembic import op

revision = 'z5a6b7c8d9e0'
down_revision = 'y4z5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── articles ──────────────────────────────────────────────────────────
    # Toutes les requêtes client filtrent par revue_id — index essentiel
    op.create_index('ix_articles_revue_id',   'articles', ['revue_id'],   if_not_exists=True)
    # HITL : filtres fréquents par statut (pending, in_review, approved…)
    op.create_index('ix_articles_status',     'articles', ['status'],     if_not_exists=True)
    # Filtre par mot-clé dans la revue de presse et le reporting
    op.create_index('ix_articles_keyword_id', 'articles', ['keyword_id'], if_not_exists=True)
    # Index composite : WHERE revue_id = ? AND status = 'approved' ORDER BY created_at DESC
    op.create_index(
        'ix_articles_revue_status',
        'articles',
        ['revue_id', 'status'],
        if_not_exists=True,
    )

    # ── rss_articles ──────────────────────────────────────────────────────
    # LEFT JOIN vers media_sources — clé de jointure
    op.create_index('ix_rss_articles_source_id', 'rss_articles', ['source_id'],           if_not_exists=True)
    # Pipeline workers filtrent par statut (pending → extracted → matched)
    op.create_index('ix_rss_articles_status',    'rss_articles', ['status'],              if_not_exists=True)
    # Dedup composite : (source_id, fingerprint)
    op.create_index(
        'ix_rss_articles_source_fingerprint',
        'rss_articles',
        ['source_id', 'content_fingerprint'],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index('ix_rss_articles_source_fingerprint', table_name='rss_articles', if_exists=True)
    op.drop_index('ix_rss_articles_status',             table_name='rss_articles', if_exists=True)
    op.drop_index('ix_rss_articles_source_id',          table_name='rss_articles', if_exists=True)
    op.drop_index('ix_articles_revue_status',           table_name='articles',     if_exists=True)
    op.drop_index('ix_articles_keyword_id',             table_name='articles',     if_exists=True)
    op.drop_index('ix_articles_status',                 table_name='articles',     if_exists=True)
    op.drop_index('ix_articles_revue_id',               table_name='articles',     if_exists=True)
