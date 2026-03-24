"""add status and collection_method to rss_articles

Revision ID: n3o4p5q6r7s8
Revises: m2n3o4p5q6r7
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = 'n3o4p5q6r7s8'
down_revision = 'm2n3o4p5q6r7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Champ status — state machine du pipeline
    op.add_column('rss_articles', sa.Column(
        'status', sa.String(20), nullable=False, server_default='pending'
    ))
    # Méthode de collecte — rss | sitemap | serpapi | playwright
    op.add_column('rss_articles', sa.Column(
        'collection_method', sa.String(20), nullable=True
    ))
    # Erreur d'extraction — paywall, timeout, etc.
    op.add_column('rss_articles', sa.Column(
        'extraction_error', sa.Text, nullable=True
    ))

    # Index sur status pour les workers
    op.create_index('idx_rss_articles_status', 'rss_articles', ['status'])

    # Backfill — migrer les données existantes vers le nouveau status
    op.execute("""
        UPDATE rss_articles SET status = 'matched'
        WHERE matched_at IS NOT NULL
    """)
    op.execute("""
        UPDATE rss_articles SET status = 'extracted'
        WHERE enriched_at IS NOT NULL AND matched_at IS NULL
    """)
    # status = 'pending' déjà par défaut pour le reste

    # Backfill collection_method — tous les articles existants viennent du RSS
    op.execute("""
        UPDATE rss_articles SET collection_method = 'rss'
        WHERE collection_method IS NULL
    """)


def downgrade() -> None:
    op.drop_index('idx_rss_articles_status', table_name='rss_articles')
    op.drop_column('rss_articles', 'extraction_error')
    op.drop_column('rss_articles', 'collection_method')
    op.drop_column('rss_articles', 'status')
