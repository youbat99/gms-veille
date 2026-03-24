"""add scheduler_slot_keywords

Revision ID: x3y4z5a6b7c8
Revises: w2x3y4z5a6b7
Create Date: 2026-03-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'x3y4z5a6b7c8'
down_revision = 'w2x3y4z5a6b7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scheduler_slot_keywords',
        sa.Column('slot_id',    postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('keyword_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['slot_id'],    ['scheduler_slot.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['keyword_id'], ['keywords.id'],       ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('slot_id', 'keyword_id'),
    )


def downgrade():
    op.drop_table('scheduler_slot_keywords')
