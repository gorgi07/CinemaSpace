import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310_000)
    return f'pbkdf2_sha256$310000${salt.hex()}${derived.hex()}'

def verify_password(password: str, saved: str) -> bool:
    try:
        kind, iterations, salt, digest = saved.split('$')
        if kind != 'pbkdf2_sha256': return False
        derived = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(derived, bytes.fromhex(digest))
    except (ValueError, TypeError): return False

def make_token(user: User) -> str:
    return jwt.encode({'sub':str(user.id),'role':user.role,'exp':datetime.now(timezone.utc)+timedelta(hours=12)}, settings.secret_key, algorithm='HS256')

async def current_user(creds: HTTPAuthorizationCredentials | None=Depends(bearer), db: AsyncSession=Depends(get_db)) -> User:
    if creds is None: raise HTTPException(401,'Необходима авторизация')
    try:
        payload=jwt.decode(creds.credentials,settings.secret_key,algorithms=['HS256'])
        user_id=int(payload['sub'])
    except (jwt.PyJWTError, KeyError, ValueError): raise HTTPException(401,'Недействительный токен')
    user=await db.get(User,user_id)
    if not user: raise HTTPException(401,'Пользователь не найден')
    return user

async def admin_user(user:User=Depends(current_user)) -> User:
    if user.role!='admin': raise HTTPException(403,'Только для администратора')
    return user
