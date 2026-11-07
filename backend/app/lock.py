import secrets
from redis.asyncio import Redis

RELEASE_SCRIPT = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end"
RENEW_SCRIPT = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE',KEYS[1],ARGV[2]) else return 0 end"

class RedisLock:
    def __init__(self, redis:Redis, key:str, ttl_ms:int=8000):
        self.redis=redis; self.key=key; self.ttl_ms=ttl_ms; self.token=None

    async def acquire(self)->bool:
        token=secrets.token_urlsafe(24)
        ok=await self.redis.set(self.key,token,nx=True,px=self.ttl_ms)
        if ok: self.token=token
        return bool(ok)

    async def renew(self)->bool:
        if not self.token: return False
        return bool(await self.redis.eval(RENEW_SCRIPT,1,self.key,self.token,self.ttl_ms))

    async def release(self)->bool:
        if not self.token: return False
        token=self.token
        self.token=None
        return bool(await self.redis.eval(RELEASE_SCRIPT,1,self.key,token))
