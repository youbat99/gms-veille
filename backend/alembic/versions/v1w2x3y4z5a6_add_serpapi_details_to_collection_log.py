"""add serpapi details to collection_log

Revision ID: v1w2x3y4z5a6
Revises: u0v1w2x3y4z5
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 'v1w2x3y4z5a6'
down_revision = 'u0v1w2x3y4z5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('collection_log', sa.Column('filtered_old', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('collection_log', sa.Column('engine',       sa.String(32),  nullable=True))
    op.add_column('collection_log', sa.Column('gl',           sa.String(8),   nullable=True))
    op.add_column('collection_log', sa.Column('language',     sa.String(8),   nullable=True))
    op.add_column('collection_log', sa.Column('sort_by',      sa.String(16),  nullable=True))
    op.add_column('collection_log', sa.Column('as_qdr',       sa.String(16),  nullable=True))
    op.add_column('collection_log', sa.Column('safe_search',  sa.Boolean(),   nullable=True))
    op.add_column('collection_log', sa.Column('num_results',  sa.Integer(),   nullable=True))
    op.add_column('collection_log', sa.Column('articles_found', JSONB,        nullable=True))


def downgrade():
    op.drop_column('collection_log', 'articles_found')
    op.drop_column('collection_log', 'num_results')
    op.drop_column('collection_log', 'safe_search')
    op.drop_column('collection_log', 'as_qdr')
    op.drop_column('collection_log', 'sort_by')
    op.drop_column('collection_log', 'language')
    op.drop_column('collection_log', 'gl')
    op.drop_column('collection_log', 'engine')
    op.drop_column('collection_log', 'filtered_old')
