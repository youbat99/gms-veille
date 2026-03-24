"""add serp config to revue_keywords

Revision ID: w2x3y4z5a6b7
Revises: v1w2x3y4z5a6
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'w2x3y4z5a6b7'
down_revision = 'v1w2x3y4z5a6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('revue_keywords', sa.Column('tbs',         sa.String(16),  nullable=True))
    op.add_column('revue_keywords', sa.Column('gl',          sa.String(8),   nullable=True))
    op.add_column('revue_keywords', sa.Column('language',    sa.String(8),   nullable=True))
    op.add_column('revue_keywords', sa.Column('num_results', sa.Integer(),   nullable=True))
    op.add_column('revue_keywords', sa.Column('sort_by',     sa.String(16),  nullable=True))
    op.add_column('revue_keywords', sa.Column('safe_search', sa.Boolean(),   nullable=True))


def downgrade():
    op.drop_column('revue_keywords', 'safe_search')
    op.drop_column('revue_keywords', 'sort_by')
    op.drop_column('revue_keywords', 'num_results')
    op.drop_column('revue_keywords', 'language')
    op.drop_column('revue_keywords', 'gl')
    op.drop_column('revue_keywords', 'tbs')
