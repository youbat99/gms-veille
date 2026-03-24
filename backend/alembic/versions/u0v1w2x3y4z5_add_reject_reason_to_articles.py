"""add reject_reason to articles

Revision ID: u0v1w2x3y4z5
Revises: t9u0v1w2x3y4
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa

revision = 'u0v1w2x3y4z5'
down_revision = 't9u0v1w2x3y4'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('articles', sa.Column('reject_reason', sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column('articles', 'reject_reason')
