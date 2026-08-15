from typing import Optional
import aiomysql
from core.config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

_pool: Optional[aiomysql.Pool] = None


async def get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            db=DB_NAME,
            autocommit=False,
            charset="utf8mb4",
            minsize=1,
            maxsize=10,
        )
    return _pool


async def get_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            try:
                yield cur
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise