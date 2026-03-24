"""add article_reads table (lu/non lu + étoile par utilisateur)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'article_reads',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('article_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('starred', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('starred_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'article_id', name='uq_article_read_user_article'),
    )
    op.create_index('ix_article_reads_user_id',    'article_reads', ['user_id'])
    op.create_index('ix_article_reads_article_id', 'article_reads', ['article_id'])


def downgrade() -> None:
    op.drop_index('ix_article_reads_article_id', table_name='article_reads')
    op.drop_index('ix_article_reads_user_id',    table_name='article_reads')
    op.drop_table('article_reads')
