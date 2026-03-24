"""make source_id nullable in rss_articles (pour SerpAPI sans MediaSource)

Revision ID: o4p5q6r7s8t9
Revises: n3o4p5q6r7s8
Create Date: 2026-03-18
"""
from alembic import op
import sqlalchemy as sa

revision = 'o4p5q6r7s8t9'
down_revision = 'n3o4p5q6r7s8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('rss_articles', 'source_id', nullable=True)


def downgrade() -> None:
    # Remettre NOT NULL — attention : des NULL peuvent exister
    op.execute("UPDATE rss_articles SET source_id = (SELECT id FROM media_sources LIMIT 1) WHERE source_id IS NULL")
    op.alter_column('rss_articles', 'source_id', nullable=False)
