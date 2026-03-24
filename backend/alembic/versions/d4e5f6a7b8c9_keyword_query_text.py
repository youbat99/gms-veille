"""keyword query field: String(1000) -> Text

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        'keywords', 'query',
        existing_type=sa.String(length=1000),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'keywords', 'query',
        existing_type=sa.Text(),
        type_=sa.String(length=1000),
        existing_nullable=True,
    )
