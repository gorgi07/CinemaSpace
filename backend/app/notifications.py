"""Main durable notification storage is Firestore; Redis retains KV features and caching."""
import asyncio
from functools import partial
from app.mail import send_mail

async def dispatch(db, redis, user, category, subject, body, *, store):
    note, _ = await asyncio.to_thread(partial(store.create, user.id, category, subject, body))
    # Business notification remains durable even when SMTP is unavailable.
    try:
        await send_mail(user.email, subject, body)
        status = 'sent'
    except Exception:
        status = 'failed'
    note = await asyncio.to_thread(partial(store.modify, note['id'], changes={'email_status': status, 'status': status},
                                           stage={'type': 'email_delivery', 'status': status, 'description': 'Результат попытки отправки SMTP'}))
    await redis.incr('cache:firestore:version:admin')
    await redis.incr(f'cache:firestore:version:user:{user.id}')
    return note
