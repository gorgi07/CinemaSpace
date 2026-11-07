import asyncio
import json
import time
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import engine, get_db, SessionLocal
from app.models import Base, User, Movie, Hall, Screening, Booking, Category, Notification, NotificationTemplate
from app.security import hash_password, verify_password, make_token, current_user, admin_user
from app.redis_store import primary, replica, connect_redis, close_redis
from app.lock import RedisLock
from app.notifications import dispatch
from app.firestore_store import NotificationStore
from functools import partial

async def seed(db):
    if not (await db.scalar(select(User).where(User.email==settings.admin_email))):
        db.add(User(email=settings.admin_email,full_name='Администратор',role='admin',password_hash=hash_password(settings.admin_password)))
    if not (await db.scalar(select(Category).limit(1))):
        db.add_all([Category(slug=x,name=y) for x,y in [('booking','Бронирования'),('schedule','Расписание'),('promotions','Акции'),('general','Общие')]])
    if not (await db.scalar(select(Movie).limit(1))):
        db.add_all([
            Movie(title='Город после дождя',genre='Драма',duration_min=112,rating='12+',description='История неожиданной встречи, которая меняет привычный ход жизни.',poster_url=''),
            Movie(title='За пределами орбиты',genre='Фантастика',duration_min=128,rating='12+',description='Космическая экспедиция открывает тайну далёкой планеты.',poster_url=''),
            Movie(title='Свет в окне',genre='Комедия',duration_min=96,rating='6+',description='Добрая городская история о соседях и маленьких приключениях.',poster_url=''),
            Movie(title='Тайна старого маяка',genre='Приключения',duration_min=104,rating='12+',description='На побережье юные исследователи находят загадочные записи.',poster_url=''),
        ])
    if not (await db.scalar(select(Hall).limit(1))):
        db.add_all([Hall(name='Зал Премьер',rows=5,seats_per_row=9),Hall(name='Зал Камерный',rows=4,seats_per_row=8)])
    await db.commit()
    if not (await db.scalar(select(Screening).limit(1))):
        movies=(await db.scalars(select(Movie).order_by(Movie.id))).all()
        halls=(await db.scalars(select(Hall).order_by(Hall.id))).all()
        day=datetime.now(timezone.utc).replace(hour=12,minute=0,second=0,microsecond=0)+timedelta(days=1)
        for i,movie in enumerate(movies):
            for j in range(2):
                db.add(Screening(movie_id=movie.id,hall_id=halls[(i+j)%len(halls)].id,start_at=day+timedelta(days=j,hours=i*2),price_cents=55000+i*5000))
        await db.commit()
    if not (await db.scalar(select(NotificationTemplate).limit(1))):
        db.add_all([
            NotificationTemplate(name='Приветствие',subject='Добро пожаловать в Cinema!',body='Спасибо, что вы с нами. Желаем приятных просмотров!',category_slug='general'),
            NotificationTemplate(name='Акция',subject='Новости кинотеатра',body='У нас появились новые сеансы. Посмотрите афишу!',category_slug='promotions'),
        ])
        await db.commit()

@asynccontextmanager
async def lifespan(app:FastAPI):
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db: await seed(db)
    await connect_redis(app)
    import os
    if settings.firestore_mode not in {'emulator', 'cloud'}:
        raise RuntimeError('FIRESTORE_MODE must be emulator or cloud')
    if settings.firestore_mode == 'cloud':
        os.environ.pop('FIRESTORE_EMULATOR_HOST', None)
    else:
        os.environ['FIRESTORE_EMULATOR_HOST'] = os.getenv('FIRESTORE_EMULATOR_HOST') or 'firestore-emulator:8080'
    app.state.notifications = NotificationStore(settings.firestore_project_id)
    # Firestore Emulator can start slightly later than the API container.
    for attempt in range(20):
        try:
            await asyncio.to_thread(app.state.notifications.seed_categories)
            break
        except Exception:
            if settings.firestore_mode == 'cloud' or attempt == 19:
                raise
            await asyncio.sleep(2)
    yield
    await close_redis(app)
    await engine.dispose()

app=FastAPI(title='Cinema Platform API',version='2.0.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in settings.cors_origins.split(',')],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

class RegisterBody(BaseModel):
    email: EmailStr
    password: str=Field(min_length=8,max_length=128)
    full_name: str=Field(min_length=2,max_length=140)
class LoginBody(BaseModel):
    # Login must accept seeded local-domain accounts (admin@cinema.local).
    # Registration still uses EmailStr for real user addresses.
    email: str = Field(min_length=3, max_length=254)
    password: str
class MovieBody(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    description:str=''
    duration_min:int=Field(ge=1,le=500)
    genre:str='Другое'
    poster_url:str=''
    rating:str='12+'
class HallBody(BaseModel):
    name:str
    rows:int=Field(ge=1,le=26)
    seats_per_row:int=Field(ge=1,le=40)
class SessionBody(BaseModel):
    movie_id:int
    hall_id:int
    start_at:datetime
    price_cents:int=Field(ge=0)
class HoldBody(BaseModel):
    seat_code:str=Field(pattern=r'^[A-Z]([1-9]|[1-3][0-9]|40)$')
class ConfirmBody(BaseModel):
    seat_code:str
    hold_token:str
class NotifyBody(BaseModel):
    user_id:int|None=None
    everyone:bool=False
    category_slug:str='general'
    subject:str=Field(min_length=1,max_length=200)
    body:str=Field(min_length=1)
class TemplateBody(BaseModel):
    name:str
    subject:str
    body:str
    category_slug:str='general'
class ExperimentBody(BaseModel):
    mode:Literal['eventual','wait_1']


def user_view(u:User): return dict(id=u.id,email=u.email,full_name=u.full_name,role=u.role)
def movie_view(m:Movie): return dict(id=m.id,title=m.title,description=m.description,genre=m.genre,duration_min=m.duration_min,poster_url=m.poster_url,rating=m.rating,active=m.active)
def screening_view(s:Screening): return dict(id=s.id,start_at=s.start_at.isoformat(),price_cents=s.price_cents,movie=movie_view(s.movie),hall={'id':s.hall.id,'name':s.hall.name,'rows':s.hall.rows,'seats_per_row':s.hall.seats_per_row})
def note_view(n:Notification): return dict(id=n.id,category_slug=n.category_slug,subject=n.subject,body=n.body,created_at=n.created_at.isoformat(),read=bool(n.read_at),email_status=n.email_status,user_id=n.user_id)

@app.get('/health')
async def health(request:Request):
    return {'status':'ok','redis':bool(await primary(request).ping())}

@app.post('/auth/register',status_code=201)
async def register(body:RegisterBody,db:AsyncSession=Depends(get_db)):
    u=User(email=body.email.lower(),full_name=body.full_name,password_hash=hash_password(body.password),role='client')
    db.add(u)
    try: await db.commit()
    except IntegrityError:
        await db.rollback(); raise HTTPException(409,'Пользователь с такой почтой уже существует')
    await db.refresh(u)
    return {'token':make_token(u),'user':user_view(u)}

@app.post('/auth/login')
async def login(body:LoginBody,db:AsyncSession=Depends(get_db)):
    user=await db.scalar(select(User).where(User.email==body.email.strip().lower()))
    if not user or not verify_password(body.password,user.password_hash): raise HTTPException(401,'Неверная почта или пароль')
    return {'token':make_token(user),'user':user_view(user)}

@app.get('/auth/me')
async def me(user:User=Depends(current_user)): return user_view(user)

@app.get('/movies')
async def movies(db:AsyncSession=Depends(get_db)):
    return [movie_view(x) for x in (await db.scalars(select(Movie).where(Movie.active==True).order_by(Movie.id))).all()]

@app.get('/screenings')
async def screenings(db:AsyncSession=Depends(get_db),movie_id:int|None=None):
    query=select(Screening).where(Screening.start_at >= datetime.now(timezone.utc)).order_by(Screening.start_at)
    if movie_id: query=query.where(Screening.movie_id==movie_id)
    rows=(await db.scalars(query)).all()
    # Explicitly load relations while session is alive (avoid async lazy-loading)
    result=[]
    for s in rows:
        await db.refresh(s,attribute_names=['movie','hall'])
        result.append(screening_view(s))
    return result

@app.get('/screenings/{screening_id}/seats')
async def seats(screening_id:int,request:Request,db:AsyncSession=Depends(get_db)):
    s=await db.get(Screening,screening_id)
    if not s: raise HTTPException(404,'Сеанс не найден')
    hall=await db.get(Hall,s.hall_id)
    confirmed=set((await db.scalars(select(Booking.seat_code).where(Booking.screening_id==screening_id,Booking.status=='confirmed'))).all())
    codes=[f'{chr(65+r)}{n}' for r in range(hall.rows) for n in range(1,hall.seats_per_row+1)]
    r=primary(request)
    holds=await r.mget([f'hold:{screening_id}:{code}' for code in codes])
    return {'screening_id':screening_id,'rows':hall.rows,'seats_per_row':hall.seats_per_row,'seats':[{'code':c,'status':'booked' if c in confirmed else 'held' if raw else 'free'} for c,raw in zip(codes,holds)]}

@app.post('/screenings/{screening_id}/hold')
async def hold(screening_id:int,body:HoldBody,request:Request,user:User=Depends(current_user),db:AsyncSession=Depends(get_db)):
    s=await db.get(Screening,screening_id)
    if not s or s.start_at<=datetime.now(timezone.utc): raise HTTPException(404,'Сеанс не найден или уже начался')
    hall=await db.get(Hall,s.hall_id)
    code=body.seat_code
    row=ord(code[0])-65
    number=int(code[1:])
    if row>=hall.rows or number>hall.seats_per_row: raise HTTPException(400,'Некорректное место')
    r=primary(request)
    lock=RedisLock(r,f'lock:seat:{screening_id}:{code}',settings.lock_ms)
    if not await lock.acquire(): raise HTTPException(423,'Место сейчас обрабатывается другим запросом')
    try:
        occupied=await db.scalar(select(Booking.id).where(Booking.screening_id==screening_id,Booking.seat_code==code,Booking.status=='confirmed'))
        if occupied: raise HTTPException(409,'Место уже забронировано')
        token=secrets.token_urlsafe(24)
        payload=json.dumps({'user_id':user.id,'token':token,'seat':code,'screening_id':screening_id})
        ok=await r.set(f'hold:{screening_id}:{code}',payload,nx=True,ex=settings.hold_seconds)
        if not ok: raise HTTPException(409,'Место временно удерживается')
        return {'hold_token':token,'seat_code':code,'ttl_seconds':settings.hold_seconds}
    finally:
        await lock.release()

@app.post('/screenings/{screening_id}/confirm',status_code=201)
async def confirm(screening_id:int,body:ConfirmBody,request:Request,user:User=Depends(current_user),db:AsyncSession=Depends(get_db)):
    r=primary(request)
    code=body.seat_code
    lock=RedisLock(r,f'lock:seat:{screening_id}:{code}',settings.lock_ms)
    if not await lock.acquire(): raise HTTPException(423,'Ресурс занят')
    try:
        key=f'hold:{screening_id}:{code}'
        raw=await r.get(key)
        if not raw: raise HTTPException(409,'Срок удержания истёк')
        data=json.loads(raw)
        if data.get('user_id')!=user.id or not secrets.compare_digest(str(data.get('token')),body.hold_token): raise HTTPException(403,'Это удержание принадлежит другому клиенту')
        screening=await db.get(Screening,screening_id)
        if not screening or screening.start_at<=datetime.now(timezone.utc): raise HTTPException(409,'Сеанс уже начался')
        booking=Booking(user_id=user.id,screening_id=screening_id,seat_code=code,status='confirmed')
        db.add(booking)
        try: await db.commit()
        except IntegrityError:
            await db.rollback(); raise HTTPException(409,'Место уже забронировано (ограничение PostgreSQL)')
        await db.refresh(booking)
        # Never use an unqualified DEL of a hold belonging to someone else.
        await r.eval("if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",1,key,raw)
    finally:
        await lock.release()
    await dispatch(db,r,user,'booking','Бронирование подтверждено',f'Место {code} на сеанс №{screening_id} успешно забронировано. Номер брони: {booking.id}.',store=request.app.state.notifications)
    return {'id':booking.id,'screening_id':screening_id,'seat_code':code,'status':'confirmed'}

@app.get('/bookings')
async def my_bookings(user:User=Depends(current_user),db:AsyncSession=Depends(get_db)):
    bookings=(await db.scalars(select(Booking).where(Booking.user_id==user.id).order_by(Booking.id.desc()))).all()
    return [{'id':b.id,'screening_id':b.screening_id,'seat_code':b.seat_code,'status':b.status,'created_at':b.created_at.isoformat()} for b in bookings]

@app.post('/bookings/{booking_id}/cancel')
async def cancel_booking(booking_id:int,request:Request,user:User=Depends(current_user),db:AsyncSession=Depends(get_db)):
    booking=await db.get(Booking,booking_id)
    if not booking or (booking.user_id!=user.id and user.role!='admin'): raise HTTPException(404,'Бронирование не найдено')
    if booking.status!='confirmed': raise HTTPException(409,'Бронирование уже отменено')
    booking.status='cancelled'
    await db.commit()
    await dispatch(db,primary(request),user,'booking','Бронирование отменено',f'Бронирование №{booking_id} отменено.',store=request.app.state.notifications)
    return {'status':'cancelled'}

async def fs(request: Request, method: str, *args, **kwargs):
    fn = getattr(request.app.state.notifications, method)
    return await asyncio.to_thread(partial(fn, *args, **kwargs))

async def cache_epoch(redis, scope: str):
    return int(await redis.get(f'cache:firestore:version:{scope}') or 0)

async def invalidate(redis, user_id: int | None = None):
    await redis.incr('cache:firestore:version:admin')
    if user_id is not None:
        await redis.incr(f'cache:firestore:version:user:{user_id}')

@app.get('/categories')
async def categories(request:Request):
    r=primary(request)
    started=time.perf_counter()
    cache_key='cache:notification-categories'
    cached=await r.get(cache_key)
    if cached:
        result=json.loads(cached);hit=True
    else:
        result=await fs(request, 'get_categories')
        await r.set(cache_key,json.dumps(result,ensure_ascii=False),ex=settings.categories_ttl_seconds)
        hit=False
    return {'categories':result,'cache':'HIT' if hit else 'MISS','ttl':await r.ttl(cache_key),'elapsed_ms':round((time.perf_counter()-started)*1000,3)}

@app.get('/notifications')
async def my_notifications(request:Request,user:User=Depends(current_user)):
    r=primary(request)
    epoch=await cache_epoch(r,f'user:{user.id}')
    key=f'cache:firestore:notifications:user:{user.id}:v{epoch}:top100'
    cached=await r.get(key)
    if cached: return json.loads(cached)
    result=await fs(request,'search',user_id=user.id,limit=100)
    await r.set(key,json.dumps(result['items'],ensure_ascii=False),ex=45)
    return result['items']

@app.post('/notifications/{note_id}/read')
async def read_notification(note_id:str,request:Request,user:User=Depends(current_user)):
    note=await fs(request,'get',note_id)
    if not note or note['user_id']!=user.id: raise HTTPException(404,'Уведомление не найдено')
    if not note['read']:
        await fs(request,'modify',note_id,changes={'read_at': datetime.now(timezone.utc)},stage={'type':'read','description':'Клиент прочитал уведомление'})
        await invalidate(primary(request),user.id)
    return {'ok':True}

@app.get('/admin/stats')
async def admin_stats(request:Request,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    result={name:await db.scalar(select(func.count()).select_from(model)) for name,model in [('users',User),('movies',Movie),('screenings',Screening),('bookings',Booking)]}
    result['notifications']=sum((await fs(request,'stats_counts')).values())
    return result

@app.get('/admin/users')
async def admin_users(_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    return [user_view(x) for x in (await db.scalars(select(User).order_by(User.id))).all()]

@app.post('/admin/movies',status_code=201)
async def admin_create_movie(body:MovieBody,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    movie=Movie(**body.model_dump()); db.add(movie);await db.commit();await db.refresh(movie);return movie_view(movie)

@app.patch('/admin/movies/{movie_id}')
async def admin_update_movie(movie_id:int,body:MovieBody,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    movie=await db.get(Movie,movie_id)
    if not movie: raise HTTPException(404,'Фильм не найден')
    for k,v in body.model_dump().items(): setattr(movie,k,v)
    await db.commit();return movie_view(movie)

@app.get('/halls')
async def halls(db:AsyncSession=Depends(get_db)):
    return [{'id':h.id,'name':h.name,'rows':h.rows,'seats_per_row':h.seats_per_row} for h in (await db.scalars(select(Hall).order_by(Hall.id))).all()]

@app.post('/admin/halls',status_code=201)
async def admin_hall(body:HallBody,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    h=Hall(**body.model_dump()); db.add(h);await db.commit();await db.refresh(h);return {'id':h.id}

@app.post('/admin/screenings',status_code=201)
async def admin_screening(body:SessionBody,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    if not await db.get(Movie,body.movie_id) or not await db.get(Hall,body.hall_id): raise HTTPException(400,'Фильм или зал не найден')
    s=Screening(**body.model_dump());db.add(s);await db.commit();await db.refresh(s);return {'id':s.id}

@app.get('/admin/templates')
async def templates(_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    return [{'id':x.id,'name':x.name,'subject':x.subject,'body':x.body,'category_slug':x.category_slug} for x in (await db.scalars(select(NotificationTemplate))).all()]

@app.post('/admin/templates',status_code=201)
async def create_template(body:TemplateBody,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    t=NotificationTemplate(**body.model_dump());db.add(t)
    try: await db.commit()
    except IntegrityError: await db.rollback();raise HTTPException(409,'Название шаблона занято')
    await db.refresh(t);return {'id':t.id}

@app.get('/admin/notifications')
async def notification_history(request:Request,_:User=Depends(admin_user)):
    return (await fs(request,'search',limit=100))['items']

@app.post('/admin/notifications/send')
async def send_notification(body:NotifyBody,request:Request,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    if body.everyone == bool(body.user_id): raise HTTPException(400,'Укажите одного пользователя либо everyone=true')
    available={x['slug'] for x in await fs(request,'get_categories')}
    if body.category_slug not in available: raise HTTPException(400,'Категория не существует')
    if body.everyone: recipients=(await db.scalars(select(User).where(User.role=='client'))).all()
    else:
        u=await db.get(User,body.user_id)
        if not u: raise HTTPException(404,'Клиент не найден')
        recipients=[u]
    for recipient in recipients: await dispatch(db,primary(request),recipient,body.category_slug,body.subject,body.body,store=request.app.state.notifications)
    return {'sent_to':len(recipients)}

class NotificationChange(BaseModel):
    subject:str|None=None
    body:str|None=None
    status:Literal['pending','processing','sent','failed']|None=None
    category_slug:str|None=None

class StageBody(BaseModel):
    type:str=Field(min_length=1,max_length=60)
    description:str=''
    status:str='completed'

class DemoNotificationBody(BaseModel):
    user_id:int
    category_slug:str='general'
    subject:str=Field(default='Тестовое уведомление Firestore',min_length=1,max_length=200)
    body:str=Field(default='Создано для исследования транзакций и аналитики.',min_length=1)

@app.post('/admin/lab/notifications/demo',status_code=201)
async def create_demo_notification(body:DemoNotificationBody,request:Request,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    user=await db.get(User,body.user_id)
    if not user: raise HTTPException(404,'Клиент не найден')
    try:
        obj,added=await fs(request,'create',user.id,body.category_slug,body.subject,body.body)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    await invalidate(primary(request),user.id)
    # Lab documents intentionally stay pending: no email is sent.
    return {'notification':obj,'email_sent':False}

@app.post('/admin/notifications/{note_id}/retry')
async def admin_retry_notification(note_id:str,request:Request,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    from app.mail import send_mail
    try:
        claimed=await fs(request,'claim_retry',note_id)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    if not claimed: raise HTTPException(404,'Уведомление не найдено')
    # This transition is committed before SMTP so parallel retry requests cannot claim it.
    user=await db.get(User,claimed['user_id'])
    try:
        if not user: raise RuntimeError('Получатель удалён')
        await send_mail(user.email,claimed['subject'],claimed['body'])
        result='sent'
    except Exception:
        result='failed'
    obj=await fs(request,'modify',note_id,changes={'status':result,'email_status':result},
                 stage={'type':'email_delivery','status':result,'description':'Результат повторной попытки SMTP'})
    await invalidate(primary(request),claimed['user_id'])
    return obj

@app.get('/admin/notifications/search')
async def search_notifications(request:Request,_:User=Depends(admin_user),status:str|None=None,category_slug:str|None=None,stage_type:str|None=None,order:Literal['asc','desc']='desc',limit:int=Query(25,ge=1,le=100),cursor:str|None=None):
    r=primary(request)
    epoch=await cache_epoch(r,'admin')
    import hashlib
    arguments=dict(status=status,category_slug=category_slug,stage_type=stage_type,order=order,limit=limit,cursor=cursor)
    digest=hashlib.sha256(json.dumps(arguments,sort_keys=True).encode()).hexdigest()
    cache_key=f'cache:firestore:notifications:admin:v{epoch}:{digest}'
    cached=await r.get(cache_key)
    if cached: return {**json.loads(cached),'cache':'HIT'}
    try: result=await fs(request,'search',**arguments)
    except ValueError as exc: raise HTTPException(400,str(exc))
    await r.set(cache_key,json.dumps(result,ensure_ascii=False),ex=45)
    return {**result,'cache':'MISS'}

@app.get('/admin/notifications/analytics')
async def notifications_analytics(request:Request,_:User=Depends(admin_user)):
    return await fs(request,'stats_counts')

@app.get('/admin/notifications/{note_id}')
async def admin_get_notification(note_id:str,request:Request,_:User=Depends(admin_user)):
    obj=await fs(request,'get',note_id)
    if not obj: raise HTTPException(404,'Уведомление не найдено')
    return obj

@app.patch('/admin/notifications/{note_id}')
async def admin_patch_notification(note_id:str,body:NotificationChange,request:Request,_:User=Depends(admin_user)):
    try: obj=await fs(request,'modify',note_id,changes=body.model_dump(exclude_none=True))
    except ValueError as exc: raise HTTPException(400,str(exc))
    if not obj: raise HTTPException(404,'Уведомление не найдено')
    await invalidate(primary(request),obj['user_id'])
    return obj

@app.post('/admin/notifications/{note_id}/stages')
async def admin_add_stage(note_id:str,body:StageBody,request:Request,_:User=Depends(admin_user)):
    obj=await fs(request,'modify',note_id,stage=body.model_dump())
    if not obj: raise HTTPException(404,'Уведомление не найдено')
    await invalidate(primary(request),obj['user_id'])
    return obj

@app.delete('/admin/notifications/{note_id}')
async def admin_delete_notification(note_id:str,request:Request,_:User=Depends(admin_user)):
    obj=await fs(request,'get',note_id)
    if not obj: raise HTTPException(404,'Уведомление не найдено')
    await fs(request,'delete',note_id)
    await invalidate(primary(request),obj['user_id'])
    return {'deleted':True}

@app.post('/admin/notifications')
async def admin_create_notification(body:NotifyBody,request:Request,_:User=Depends(admin_user),db:AsyncSession=Depends(get_db)):
    if not body.user_id: raise HTTPException(400,'Укажите user_id')
    user=await db.get(User,body.user_id)
    if not user: raise HTTPException(404,'Клиент не найден')
    obj=await dispatch(db,primary(request),user,body.category_slug,body.subject,body.body,store=request.app.state.notifications)
    return obj

@app.get('/admin/lab/replica')
async def lab_replica(request:Request,_:User=Depends(admin_user)):
    info=await replica(request).info('replication')
    return {'role':info.get('role'),'master_link_status':info.get('master_link_status'),'master_host':info.get('master_host'),'master_sync_in_progress':info.get('master_sync_in_progress')}

@app.post('/admin/lab/replica/{action}')
async def lab_replica_action(action:Literal['disconnect','reconnect'],request:Request,_:User=Depends(admin_user)):
    r=replica(request)
    if action=='disconnect': await r.execute_command('REPLICAOF','NO','ONE')
    else: await r.execute_command('REPLICAOF',settings.redis_replica_host,6379)
    return {'action':action,'status':'ok'}

@app.post('/admin/lab/consistency')
async def lab_consistency(body:ExperimentBody,request:Request,_:User=Depends(admin_user)):
    p,r=primary(request),replica(request)
    key='experiment:'+secrets.token_hex(10); value=secrets.token_urlsafe(12)
    start=time.perf_counter(); acks=None
    if body.mode=='wait_1':
        async with p.pipeline(transaction=False) as pipe:
            pipe.set(key,value,ex=120)
            pipe.wait(1,1000)
            result=await pipe.execute()
            acks=int(result[1])
    else: await p.set(key,value,ex=120)
    written=time.perf_counter()
    seen=await r.get(key)
    read_at=time.perf_counter()
    return {'mode':body.mode,'key':key,'primary_value':value,'replica_value':seen,'replica_has_current_value':seen==value,'acknowledged_replicas':acks,'write_wait_ms':round((written-start)*1000,3),'read_ms':round((read_at-written)*1000,3)}

@app.get('/admin/lab/keys')
async def lab_keys(request:Request,_:User=Depends(admin_user),pattern:str=Query('hold:*',max_length=100)):
    prefixes=('hold:','lock:seat:','cache:','notifications:unread:','experiment:')
    if not pattern.startswith(prefixes): raise HTTPException(400,'Недопустимый шаблон')
    r=primary(request);keys=[]
    async for k in r.scan_iter(match=pattern,count=100):
        keys.append({'key':k,'type':await r.type(k),'ttl':await r.ttl(k)})
        if len(keys)>=100: break
    return keys

@app.post('/admin/lab/cache/clear')
async def clear_cache(request:Request,_:User=Depends(admin_user)):
    return {'deleted':await primary(request).delete('cache:notification-categories')}

@app.get('/admin/lab/persistence')
async def persistence(request:Request,_:User=Depends(admin_user)):
    info=await primary(request).info('persistence')
    return {k:info.get(k) for k in ['aof_enabled','rdb_last_bgsave_status','aof_last_write_status','rdb_changes_since_last_save']}

@app.post('/admin/lab/lock-race')
async def lab_lock_race(request:Request,_:User=Depends(admin_user)):
    """Demonstrate exclusive acquisition with two simultaneous contenders."""
    r=primary(request)
    key='lock:seat:lab:'+secrets.token_hex(8)
    async def contender(name:str):
        lock=RedisLock(r,key,3000)
        acquired=await lock.acquire()
        if acquired:
            # Hold briefly so the other contender cannot acquire after release.
            await asyncio.sleep(0.15)
            await lock.release()
        return {'contender':name,'acquired':acquired}
    results=await asyncio.gather(contender('backend-A'),contender('backend-B'))
    return {'key':key,'contenders':results,'winners':sum(x['acquired'] for x in results)}
