"""add source_crawl_log table

Revision ID: t9u0v1w2x3y4
Revises: s8t9u0v1w2x3
Create Date: 2026-03-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = 't9u0v1w2x3y4'
down_revision = 's8t9u0v1w2x3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'source_crawl_log',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('source_id', UUID(as_uuid=True),
                  sa.ForeignKey('media_sources.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('crawled_at', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('trigger', sa.String(16), nullable=False, default='scheduled'),
        sa.Column('new_articles', sa.Integer(), nullable=False, default=0),
        sa.Column('total_found', sa.Integer(), nullable=False, default=0),
        sa.Column('duplicates', sa.Integer(), nullable=False, default=0),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_table('source_crawl_log')
