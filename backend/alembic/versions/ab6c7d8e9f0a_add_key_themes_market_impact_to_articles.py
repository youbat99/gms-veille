"""add key_themes and market_impact to articles

Revision ID: ab6c7d8e9f0a
Revises: aa6b7c8d9e0f
Create Date: 2026-03-23

Ces deux champs sont générés par Claude lors de l'enrichissement NLP
mais n'étaient pas stockés — ils étaient perdus après chaque appel.
key_themes  → liste de 3-5 thèmes principaux développés dans l'article
market_impact → 1-2 phrases sur l'impact pour les décideurs économiques
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'ab6c7d8e9f0a'
down_revision = 'aa6b7c8d9e0f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'articles',
        sa.Column('key_themes', postgresql.JSON(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        'articles',
        sa.Column('market_impact', sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('articles', 'market_impact')
    op.drop_column('articles', 'key_themes')
