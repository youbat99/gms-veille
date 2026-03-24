"""add detected_language and content_fingerprint to rss_articles

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-03-17 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'j9k0l1m2n3o4'
down_revision = 'i8j9k0l1m2n3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Langue détectée par trafilatura lors de l'enrichissement
    op.add_column('rss_articles',
        sa.Column('detected_language', sa.String(10), nullable=True)
    )
    # SimHash 64-bit (16 hex chars) pour la déduplication near-duplicate
    op.add_column('rss_articles',
        sa.Column('content_fingerprint', sa.String(16), nullable=True)
    )
    # Index pour les lookups near-dedup par source
    op.create_index(
        'ix_rss_articles_source_fingerprint',
        'rss_articles',
        ['source_id', 'content_fingerprint'],
    )


def downgrade() -> None:
    op.drop_index('ix_rss_articles_source_fingerprint', table_name='rss_articles')
    op.drop_column('rss_articles', 'content_fingerprint')
    op.drop_column('rss_articles', 'detected_language')
