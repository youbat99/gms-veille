"""add theme and weak_signal to articles

Revision ID: k0l1m2n3o4p5
Revises: j9k0l1m2n3o4
Create Date: 2026-03-17 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'k0l1m2n3o4p5'
down_revision = 'j9k0l1m2n3o4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Thématique de l'article (politique, économie, société, sport, culture, international)
    op.add_column('articles',
        sa.Column('theme', sa.String(50), nullable=True)
    )
    # Signal faible / crise potentielle détectée par le LLM
    op.add_column('articles',
        sa.Column('weak_signal', sa.Boolean(), nullable=False, server_default=sa.false())
    )
    # Index pour filtrer par thème rapidement
    op.create_index('ix_articles_theme', 'articles', ['theme'])
    # Index pour remonter les signaux faibles
    op.create_index('ix_articles_weak_signal', 'articles', ['weak_signal'])


def downgrade() -> None:
    op.drop_index('ix_articles_weak_signal', table_name='articles')
    op.drop_index('ix_articles_theme', table_name='articles')
    op.drop_column('articles', 'weak_signal')
    op.drop_column('articles', 'theme')
