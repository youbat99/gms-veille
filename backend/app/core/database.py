from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from .config import settings

# asyncpg ne supporte pas ?sslmode= dans l'URL — passer ssl via connect_args
_db_url = settings.DATABASE_URL
_connect_args: dict = {}
if "sslmode=require" in _db_url:
    _db_url = _db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = True

engine = create_async_engine(_db_url, echo=False, connect_args=_connect_args)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
