"""add matched_at to rss_articles

Revision ID: h7i8j9k0l1m2
Revises: g3h4i5j6k7l8
Create Date: 2026-03-16 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'h7i8j9k0l1m2'
down_revision = 'g3h4i5j6k7l8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('rss_articles',
        sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True)
    )
    # Index pour le worker de matching (filtre matched_at IS NULL)
    op.create_index('ix_rss_articles_matched_at', 'rss_articles', ['matched_at'])


def downgrade() -> None:
    op.drop_index('ix_rss_articles_matched_at', table_name='rss_articles')
    op.drop_column('rss_articles', 'matched_at')
