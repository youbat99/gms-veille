"""add query_json to keywords

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-03-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = 's8t9u0v1w2x3'
down_revision = 'r7s8t9u0v1w2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('keywords', sa.Column('query_json', JSONB(), nullable=True))


def downgrade():
    op.drop_column('keywords', 'query_json')
