"""Unit checks without a running Redis server."""
import pytest
from app.lock import RedisLock
from app.security import hash_password,verify_password

class FakeRedis:
    def __init__(self): self.items={}
    async def set(self,key,value,nx=False,px=None):
        if nx and key in self.items: return None
        self.items[key]=value
        return True
    async def eval(self,script,count,key,*args):
        if self.items.get(key)!=args[0]: return 0
        if 'PEXPIRE' in script: return 1
        del self.items[key]
        return 1

@pytest.mark.asyncio
async def test_only_one_holder_and_safe_release():
    r=FakeRedis()
    first=RedisLock(r,'lock:seat:test',5000)
    second=RedisLock(r,'lock:seat:test',5000)
    assert await first.acquire() is True
    assert await second.acquire() is False
    assert await first.renew() is True
    assert await first.release() is True
    assert await second.acquire() is True
    assert await first.release() is False
    assert 'lock:seat:test' in r.items
    assert await second.release() is True

def test_password_storage():
    hashed=hash_password('MySecurePassword123!')
    assert 'MySecurePassword123!' not in hashed
    assert verify_password('MySecurePassword123!',hashed)
    assert not verify_password('incorrect',hashed)
