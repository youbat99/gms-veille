"""add crawl_method to media_sources

Revision ID: i8j9k0l1m2n3
Revises: h7i8j9k0l1m2
Create Date: 2026-03-17 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'i8j9k0l1m2n3'
down_revision = 'h7i8j9k0l1m2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'media_sources',
        sa.Column('crawl_method', sa.String(20), nullable=False, server_default='rss')
    )


def downgrade() -> None:
    op.drop_column('media_sources', 'crawl_method')
