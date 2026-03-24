"""add_enriched_at_to_rss_articles

Revision ID: a8f3e2b1c9d5
Revises: 097e52d4c126
Create Date: 2026-03-15 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8f3e2b1c9d5'
down_revision: Union[str, None] = '097e52d4c126'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rss_articles', sa.Column('enriched_at', sa.DateTime(timezone=True), nullable=True))
    # Index pour que le worker d'enrichissement trouve vite les articles en attente
    op.create_index('ix_rss_articles_enriched_at', 'rss_articles', ['enriched_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rss_articles_enriched_at', table_name='rss_articles')
    op.drop_column('rss_articles', 'enriched_at')
