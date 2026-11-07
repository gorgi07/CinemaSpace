import redis.asyncio as redis
from fastapi import Request
from app.config import settings

def primary(request:Request): return request.app.state.primary

def replica(request:Request): return request.app.state.replica

async def connect_redis(app):
    app.state.primary=redis.from_url(settings.redis_primary_url,decode_responses=True, socket_timeout=3)
    app.state.replica=redis.from_url(settings.redis_replica_url,decode_responses=True, socket_timeout=3)
    await app.state.primary.ping()
    await app.state.replica.ping()

async def close_redis(app):
    await app.state.primary.aclose()
    await app.state.replica.aclose()
