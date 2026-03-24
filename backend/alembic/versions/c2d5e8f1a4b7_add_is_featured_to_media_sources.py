"""add_is_featured_to_media_sources

Revision ID: c2d5e8f1a4b7
Revises: b9e4f1a2c3d6
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c2d5e8f1a4b7'
down_revision: Union[str, None] = 'b9e4f1a2c3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Sources à marquer comme importantes
_FEATURED_FRAGMENTS = [
    # Arabe
    "hespress.com", "alyaoum24.com", "hibapress.com", "badil.info",
    "goud.ma", "parlement.ma", "anbaexpress.com", "alayam24.com",
    "febrayer.com", "panorapost.com", "al3omk.com", "zankat20.com",
    "alawalpress.com", "lakome2.com", "alakhbar.press.ma", "assabah.ma",
    "almassae.press.ma", "ahdath.info", "aleqtissadi.com", "eco.al3omk.com",
    "ar.medias24.com", "iktissadkom.com", "chouftv.ma", "kech24.com",
    "akhbarona.com", "hesport.com", "almountakhab.com", "tanja24.com",
    "marrakechalaan.com", "agadir24.info",
    # Français
    "fr.le360.ma", "fr.hespress.com", "telquel.ma", "ledesk.ma",
    "yabiladi.com", "bladi.net", "lnt.ma", "lanouvelletribune.info",
    "lereporter.ma", "lobservateur.info", "maroc-hebdo.press.ma",
    "medias24.com", "leconomiste.com", "lavieeco.com", "challenge.ma",
    "financenews.press.ma", "leseco.ma", "boursenews.ma", "lematin.ma",
    "aujourdhui.ma", "libe.ma", "lopinion.ma", "albayane.press.ma",
    "maroc-diplomatique.net", "atlasinfo.fr", "marocactu.com",
    "chantiersdumaroc.ma", "agri-mag.com", "industriemagazine.ma",
    "diplomatica.ma",
]

def upgrade() -> None:
    op.add_column('media_sources', sa.Column('is_featured', sa.Boolean(), nullable=False, server_default='false'))
    conn = op.get_bind()
    for fragment in _FEATURED_FRAGMENTS:
        conn.execute(sa.text(
            "UPDATE media_sources SET is_featured = true WHERE base_url LIKE :pattern"
        ), {"pattern": f"%{fragment}%"})

def downgrade() -> None:
    op.drop_column('media_sources', 'is_featured')
