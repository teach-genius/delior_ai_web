from __future__ import annotations
import logging
import os

from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.store.redis.aio import AsyncRedisStore
from dotenv import load_dotenv

load_dotenv()

logger    = logging.getLogger(__name__)
REDIS_URL = os.getenv("REDIS_URL")

async def get_checkpointer() -> AsyncRedisSaver:
    saver = AsyncRedisSaver(redis_url=REDIS_URL)
    await saver.setup()
    return saver

async def get_store() -> AsyncRedisStore:
    store = AsyncRedisStore(redis_url=REDIS_URL)
    await store.setup()
    return store