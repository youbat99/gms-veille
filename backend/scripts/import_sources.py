"""Import des sources RSS détectées dans la base de données."""
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.media_source import MediaSource
from app.services.rss_service import get_logo_url


async def main():
    sources_path = "/Users/josep/Desktop/Claude Code/Projet GMS/rss-crawler/sources.json"
    with open(sources_path, encoding="utf-8") as f:
        sources = json.load(f)

    async with AsyncSessionLocal() as db:
        added = 0
        skipped = 0
        for s in sources:
            if not s.get("rss_url"):
                continue

            base_url = s["base_url"]
            existing = await db.execute(select(MediaSource).where(MediaSource.base_url == base_url))
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            source = MediaSource(
                name=s["support"],
                base_url=base_url,
                rss_url=s["rss_url"],
                logo_url=get_logo_url(base_url),
                rss_type="natif" if s.get("source") != "Google News (fallback)" else "google_news",
                is_active=True,
            )
            db.add(source)
            added += 1

        await db.commit()
        print(f"✅ {added} sources importées | {skipped} déjà existantes")


if __name__ == "__main__":
    asyncio.run(main())
