"""add_language_to_media_sources

Revision ID: b9e4f1a2c3d6
Revises: a8f3e2b1c9d5
Create Date: 2026-03-15 23:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b9e4f1a2c3d6'
down_revision: Union[str, None] = 'a8f3e2b1c9d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Sources francophones connues (base_url fragments)
_FRENCH_URLS = [
    "fr.le360.ma", "fr.hespress.com", "fr.hibapress.com",
    "h24info.ma", "aujourdhui.ma", "challenge.ma", "ecoactu.ma",
    "femmesdumaroc.com", "france24.com", "industries.ma",
    "infomediaire.net", "leconomiste.com", "lessentielinfo.com",
    "linformation.ma", "lobservateur.info", "lopinion.ma",
    "lnt.ma", "laquotidienne.ma", "lareleve.ma", "latribune.ma",
    "laverite.ma", "lavieeco.com", "le12.ma", "le212news.ma",
    "lebrief.ma", "ledesk.ma", "lematin.ma", "lenouvelliste.ma",
    "le360sport.ma", "leseco.ma", "lesiteinfo.com", "liberation.ma",
    "maghrebeco.com", "maghreb-intelligence.com", "maroc.ma",
    "medias24.com", "atlasinfo.fr", "tv5monde.com",
    "albayane.press.ma", "consonews.ma", "mednews.ma",
    "libe.ma", "telquel.ma", "bladi.net", "lereporter.ma",
    "lanouvelletribune.info", "maroc-hebdo.press.ma",
    "financenews.press.ma", "boursenews.ma",
    "maroc-diplomatique.net", "marocactu.com",
    "chantiersdumaroc.ma", "agri-mag.com", "industriemagazine.ma",
    "diplomatica.ma", "aemagazine.ma", "yabiladi.com",
]

# Sources anglophones connues
_ENGLISH_URLS = [
    "en.hespress.com", "en.yabiladi.com", "alarab.co.uk",
    "alquds.co.uk", "atarab.co.uk",
]


def upgrade() -> None:
    op.add_column(
        'media_sources',
        sa.Column('language', sa.String(5), nullable=False, server_default='ar')
    )

    # Mettre à jour les sources francophones
    conn = op.get_bind()
    for fragment in _FRENCH_URLS:
        conn.execute(sa.text(
            "UPDATE media_sources SET language = 'fr' WHERE base_url LIKE :pattern"
        ), {"pattern": f"%{fragment}%"})

    # Mettre à jour les sources anglophones
    for fragment in _ENGLISH_URLS:
        conn.execute(sa.text(
            "UPDATE media_sources SET language = 'en' WHERE base_url LIKE :pattern"
        ), {"pattern": f"%{fragment}%"})


def downgrade() -> None:
    op.drop_column('media_sources', 'language')
