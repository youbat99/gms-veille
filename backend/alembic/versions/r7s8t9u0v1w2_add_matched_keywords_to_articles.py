"""add matched_keywords to articles

Revision ID: r7s8t9u0v1w2
Revises: q6r7s8t9u0v1
Create Date: 2026-03-19

Stocke tous les keywords qui ont matché pour un article donné,
sous forme JSON : [{id, term, score}, ...]
Permet au validateur de choisir le keyword le plus pertinent.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r7s8t9u0v1w2"
down_revision = "q6r7s8t9u0v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column(
            "matched_keywords",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Liste de tous les keywords matchés : [{id, term, score}]",
        ),
    )


def downgrade() -> None:
    op.drop_column("articles", "matched_keywords")
